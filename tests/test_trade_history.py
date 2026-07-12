from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from agent.api import create_app
from agent.config import AgentMode, Settings
from agent.history import TradeRecord, calculate_trade_metrics, reconstruct_closed_trades
from agent.hyperliquid import HyperliquidMarketData
from agent.service import AgentService


BASE_MS = 1_700_000_000_000


def _fill(
    tid: int,
    *,
    time: int,
    side: str,
    direction: str,
    size: float,
    price: float,
    pnl: float | None,
    fee: float,
    cloid: str = "",
    start_position: float = 0,
    symbol: str = "BTC",
) -> dict:
    result = {
        "coin": symbol,
        "side": side,
        "dir": direction,
        "sz": str(size),
        "px": str(price),
        "fee": str(fee),
        "time": time,
        "tid": tid,
        "cloid": cloid,
        "startPosition": str(start_position),
    }
    if pnl is not None:
        result["closedPnl"] = str(pnl)
    return result


def _local_context() -> tuple[list[dict], list[dict], list[dict]]:
    cycle_id = "00000000-0000-0000-0000-000000000777"
    intents = [
        {
            "intent_id": "open-btc",
            "cycle_id": cycle_id,
            "cloid": "0xopen",
            "symbol": "BTC",
            "action": "OPEN",
            "direction": "LONG",
            "payload": {"leverage": 4},
        }
    ]
    protections = [
        {"cloid": "0xtp", "symbol": "BTC", "kind": "TP"},
        {"cloid": "0xsl", "symbol": "BTC", "kind": "SL"},
    ]
    cycles = [
        {
            "cycle_id": cycle_id,
            "state": {
                "decision": {
                    "playbook": {
                        "payload": {
                            "plans": [
                                {
                                    "symbol": "BTC",
                                    "thesis": "Momentum should continue while the invalidation remains intact.",
                                }
                            ]
                        }
                    },
                    "trader": {
                        "decisions": [
                            {
                                "symbol": "BTC",
                                "action": "OPEN",
                                "rationale": "Price and liquidity confirm the planned long entry.",
                            }
                        ]
                    },
                }
            },
        }
    ]
    return intents, protections, cycles


def test_reconstructs_long_with_partial_tp_final_sl_and_local_explanation() -> None:
    intents, protections, cycles = _local_context()
    fills = [
        _fill(
            1,
            time=BASE_MS,
            side="B",
            direction="Open Long",
            size=2,
            price=100,
            pnl=0,
            fee=1,
            cloid="0xopen",
        ),
        _fill(
            2,
            time=BASE_MS + 3_600_000,
            side="A",
            direction="Close Long",
            size=0.5,
            price=110,
            pnl=5,
            fee=0.25,
            cloid="0xtp",
            start_position=2,
        ),
        _fill(
            3,
            time=BASE_MS + 7_200_000,
            side="A",
            direction="Close Long",
            size=1.5,
            price=95,
            pnl=-7.5,
            fee=0.75,
            cloid="0xsl",
            start_position=1.5,
        ),
    ]
    funding = [
        {
            "time": BASE_MS + 1_800_000,
            "delta": {"coin": "BTC", "usdc": "-0.5"},
        },
        {
            "time": BASE_MS + 1_800_000,
            "delta": {"coin": "ETH", "usdc": "50"},
        },
    ]

    trades = reconstruct_closed_trades(
        fills,
        funding_records=funding,
        intents=intents,
        protections=protections,
        cycles=cycles,
    )

    assert len(trades) == 1
    trade = trades[0]
    assert trade.symbol == "BTC"
    assert trade.side == "LONG"
    assert trade.avg_entry_px == 100
    assert trade.avg_exit_px == pytest.approx(98.75)
    assert trade.initial_size == 2
    assert trade.initial_notional_usd == 200
    assert trade.leverage == 4
    assert trade.gross_pnl_usd == pytest.approx(-2.5)
    assert trade.fees_usd == pytest.approx(2)
    assert trade.funding_usd == pytest.approx(-0.5)
    assert trade.funding_source == "hyperliquid_user_funding"
    assert trade.net_pnl_usd == pytest.approx(-5)
    assert trade.price_return_pct == pytest.approx(-1.25)
    assert trade.margin_return_pct == pytest.approx(-10)
    assert trade.close_reason == "SL"
    assert trade.outcome == "LOSS"
    assert trade.thesis and "Momentum" in trade.thesis
    assert trade.rationale and "liquidity" in trade.rationale
    assert trade.source.endswith("00000000-0000-0000-0000-000000000777")


def test_reconstructs_short_partial_exits_and_computes_missing_closed_pnl() -> None:
    fills = [
        _fill(
            10,
            time=BASE_MS,
            side="A",
            direction="Open Short",
            size=3,
            price=100,
            pnl=0,
            fee=0.3,
        ),
        _fill(
            11,
            time=BASE_MS + 1_000,
            side="B",
            direction="Close Short",
            size=1,
            price=90,
            pnl=None,
            fee=0.1,
            start_position=-3,
        ),
        _fill(
            12,
            time=BASE_MS + 2_000,
            side="B",
            direction="Close Short",
            size=2,
            price=80,
            pnl=None,
            fee=0.2,
            start_position=-2,
        ),
    ]

    trade = reconstruct_closed_trades(fills, funding_records=None)[0]

    assert trade.side == "SHORT"
    assert trade.avg_exit_px == pytest.approx(250 / 3)
    assert trade.gross_pnl_usd == pytest.approx(50)
    assert trade.net_pnl_usd == pytest.approx(49.4)
    assert trade.price_return_pct == pytest.approx(100 / 6)
    assert trade.close_reason == "MANUAL"
    assert trade.funding_usd == 0
    assert trade.funding_source == "unavailable"


def test_missing_closed_pnl_uses_the_remaining_position_cost_after_a_scale_in() -> None:
    fills = [
        _fill(13, time=BASE_MS, side="B", direction="Open Long", size=1, price=100, pnl=0, fee=0),
        _fill(14, time=BASE_MS + 1_000, side="A", direction="Close Long", size=0.5, price=110, pnl=None, fee=0, start_position=1),
        _fill(15, time=BASE_MS + 2_000, side="B", direction="Open Long", size=1, price=120, pnl=0, fee=0, start_position=0.5),
        _fill(16, time=BASE_MS + 3_000, side="A", direction="Close Long", size=1.5, price=110, pnl=None, fee=0, start_position=1.5),
    ]

    trade = reconstruct_closed_trades(fills, funding_records=[])[0]

    assert trade.avg_entry_px == 110
    assert trade.avg_exit_px == 110
    assert trade.gross_pnl_usd == pytest.approx(0)
    assert trade.outcome == "BREAK_EVEN"


def test_direct_reversal_closes_one_round_trip_and_opens_the_other() -> None:
    fills = [
        _fill(
            20,
            time=BASE_MS,
            side="B",
            direction="Open Long",
            size=1,
            price=100,
            pnl=0,
            fee=1,
        ),
        _fill(
            21,
            time=BASE_MS + 1_000,
            side="A",
            direction="Close Long",
            size=2,
            price=90,
            pnl=-10,
            fee=2,
            start_position=1,
        ),
        _fill(
            22,
            time=BASE_MS + 2_000,
            side="B",
            direction="Close Short",
            size=1,
            price=80,
            pnl=10,
            fee=1,
            start_position=-1,
        ),
    ]

    trades = reconstruct_closed_trades(fills, funding_records=[])

    assert [item.side for item in trades] == ["SHORT", "LONG"]
    short, long = trades
    assert short.gross_pnl_usd == 10
    assert short.fees_usd == 2
    assert long.gross_pnl_usd == -10
    assert long.fees_usd == 2


def test_reconstruction_is_idempotent_deduplicates_exchange_ids_and_omits_open_trades() -> None:
    opening = _fill(
        30,
        time=BASE_MS,
        side="B",
        direction="Open Long",
        size=1,
        price=100,
        pnl=0,
        fee=0.1,
    )
    closing = _fill(
        31,
        time=BASE_MS + 1_000,
        side="A",
        direction="Close Long",
        size=1,
        price=110,
        pnl=10,
        fee=0.1,
        start_position=1,
    )
    fills = [opening, opening.copy(), closing, closing.copy()]

    first = reconstruct_closed_trades(fills, funding_records=[])
    second = reconstruct_closed_trades(list(reversed(fills)), funding_records=[])

    assert first == second
    assert len(first) == 1
    assert first[0].initial_size == 1
    assert reconstruct_closed_trades([opening], funding_records=[]) == []
    assert reconstruct_closed_trades([closing], funding_records=[]) == []

    anonymous_open = {key: value for key, value in opening.items() if key != "tid"}
    anonymous_close = {key: value for key, value in closing.items() if key != "tid"}
    anonymous = reconstruct_closed_trades(
        [anonymous_open, anonymous_open.copy(), anonymous_close, anonymous_close.copy()],
        funding_records=[],
    )
    assert len(anonymous) == 1
    assert anonymous[0].initial_size == 1


def _record(index: int, pnl: float, margin_return: float) -> TradeRecord:
    opened = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(hours=index)
    return TradeRecord(
        trade_id=f"trade-{index}",
        symbol="BTC",
        side="LONG",
        opened_at=opened,
        closed_at=opened + timedelta(minutes=30),
        avg_entry_px=100,
        avg_exit_px=101,
        initial_size=1,
        initial_notional_usd=100,
        leverage=1,
        gross_pnl_usd=pnl,
        fees_usd=0,
        funding_usd=0,
        funding_source="unavailable",
        net_pnl_usd=pnl,
        price_return_pct=1,
        margin_return_pct=margin_return,
        close_reason="MANUAL",
        outcome="PROFIT" if pnl > 0 else "LOSS",
        source="fixture",
    )


def test_metrics_cover_profit_factor_and_realised_peak_to_trough_drawdown() -> None:
    metrics = calculate_trade_metrics(
        [_record(1, 10, 10), _record(2, -4, -4), _record(3, -8, -8), _record(4, 3, 3)]
    )

    assert metrics.total_trades == 4
    assert metrics.win_rate_pct == 50
    assert metrics.avg_win_usd == pytest.approx(6.5)
    assert metrics.avg_loss_usd == pytest.approx(-6)
    assert metrics.profit_factor == pytest.approx(13 / 12)
    assert metrics.cumulative_net_pnl_usd == 1
    assert metrics.max_drawdown_usd == 12
    assert metrics.max_drawdown_pct == 12


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        agent_mode=AgentMode.PAPER,
        database_url="sqlite://",
        llm_provider="rules",
    )


def test_trade_api_contract_is_empty_without_an_exchange_account() -> None:
    with TestClient(create_app(_settings())) as client:
        history = client.get("/api/trades")
        metrics = client.get("/api/trades/metrics")

    assert history.status_code == 200
    assert history.json() == {"trades": [], "total": 0, "as_of": None}
    assert metrics.status_code == 200
    assert metrics.json() == {
        "total_trades": 0,
        "win_rate_pct": 0.0,
        "avg_win_usd": 0.0,
        "avg_loss_usd": 0.0,
        "profit_factor": None,
        "cumulative_net_pnl_usd": 0.0,
        "max_drawdown_usd": 0.0,
        "max_drawdown_pct": 0.0,
    }


class _FakeInfoClient:
    def __init__(self, fills: list[dict]) -> None:
        self.fills = fills
        self.fill_calls = 0
        self.funding_calls = 0

    def user_fills(self, _: str) -> list[dict]:
        self.fill_calls += 1
        return self.fills

    def user_funding(self, _: str, __: int, ___: int) -> list[dict]:
        self.funding_calls += 1
        return []


def test_agent_service_fetches_once_and_metrics_reuse_the_computed_view() -> None:
    service = AgentService(_settings())
    service.settings.trade_history_start_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
    client = _FakeInfoClient(
        [
            _fill(
                40,
                time=BASE_MS,
                side="B",
                direction="Open Long",
                size=1,
                price=100,
                pnl=0,
                fee=0.1,
            ),
            _fill(
                41,
                time=BASE_MS + 1_000,
                side="A",
                direction="Close Long",
                size=1,
                price=110,
                pnl=10,
                fee=0.1,
                start_position=1,
            ),
        ]
    )
    service.market = HyperliquidMarketData(client, "mainnet", "0xaccount")

    history = service.trade_history()
    metrics = service.trade_metrics()

    assert history["total"] == 1
    assert history["as_of"]
    assert metrics["total_trades"] == 1
    assert client.fill_calls == 1
    assert client.funding_calls == 1
