from __future__ import annotations

from datetime import datetime, timezone

import pytest
from eth_account import Account
from pydantic import ValidationError

from agent.config import Settings
from agent.db import build_engine, build_session_factory
from agent.domain import ApprovedOrder
from agent.hyperliquid_execution import (
    HyperliquidTestnetExecutionService,
    HyperliquidTestnetReadiness,
)
from agent.repository import Repository

TEST_KEY = "0x" + "1" * 64
MASTER_ADDRESS = "0x" + "2" * 40


class FakeInfo:
    name_to_coin = {"BTC": "BTC", "ETH": "ETH", "SOL": "SOL"}
    coin_to_asset = {"BTC": 0, "ETH": 1, "SOL": 2}
    asset_to_sz_decimals = {0: 5, 1: 4, 2: 2}

    def __init__(
        self,
        *,
        account_value: float = 100,
        order_status: str = "filled",
        abstraction: str = "disabled",
    ):
        self.account_value = account_value
        self.order_status = order_status
        self.abstraction = abstraction
        self.queries = []
        self.asset_positions = []
        self.frontend_orders = []
        self.frontend_orders_calls = 0
        self.frontend_orders_fail_after = None
        self.order_responses = {}

    def extra_agents(self, _account):
        return [{"address": Account.from_key(TEST_KEY).address, "name": "pytest"}]

    def user_role(self, _signer):
        return {"role": "agent", "data": {"user": MASTER_ADDRESS}}

    def user_state(self, _account):
        return {
            "marginSummary": {"accountValue": str(self.account_value)},
            "withdrawable": str(self.account_value),
            "assetPositions": self.asset_positions,
        }

    def frontend_open_orders(self, _account):
        self.frontend_orders_calls += 1
        if (
            self.frontend_orders_fail_after is not None
            and self.frontend_orders_calls > self.frontend_orders_fail_after
        ):
            raise RuntimeError("open-order verification unavailable")
        return self.frontend_orders

    def query_user_abstraction_state(self, _account):
        return self.abstraction

    def spot_user_state(self, _account):
        return {
            "balances": [
                {"coin": "USDC", "token": 0, "total": str(self.account_value), "hold": "0"}
            ],
            "tokenToAvailableAfterMaintenance": [[0, str(self.account_value)]],
        }

    def query_order_by_cloid(self, account, cloid):
        self.queries.append((account, str(cloid)))
        if str(cloid) in self.order_responses:
            return self.order_responses[str(cloid)]
        return {"status": "order", "orderStatus": self.order_status}


class FakeExchange:
    def __init__(self, info, response=None, error=None):
        self.info = info
        self.response = response or {
            "status": "ok",
            "response": {"data": {"statuses": [{"filled": {"oid": 42}}]}},
        }
        self.error = error
        self.calls = []
        self.bulk_calls = []
        self.cancel_calls = []
        self.leverage_calls = []

    def update_leverage(self, leverage, symbol, is_cross=False):
        self.leverage_calls.append((leverage, symbol, is_cross))
        return {"status": "ok", "response": {"type": "default"}}

    def order(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.error:
            raise self.error
        return self.response

    def bulk_orders(self, requests, grouping="na"):
        self.bulk_calls.append((requests, grouping))
        if self.error:
            raise self.error
        return self.response

    def bulk_cancel_by_cloid(self, requests):
        self.cancel_calls.append(requests)
        return {"status": "ok", "response": {"type": "cancel"}}


def repository() -> Repository:
    engine = build_engine("sqlite://")
    repo = Repository(engine, build_session_factory(engine))
    repo.initialize()
    repo.create_cycle("00000000-0000-0000-0000-000000000101", "testnet", datetime.now(timezone.utc))
    return repo


def order() -> ApprovedOrder:
    return ApprovedOrder(
        cycle_id="00000000-0000-0000-0000-000000000101",
        playbook_id="pb-testnet",
        symbol="BTC",
        action="OPEN",
        direction="LONG",
        notional_usd=25,
        mark_px=65_000,
        invalidation_px=63_500,
        targets=[66_000, 67_000, 68_000],
        leverage=4,
        decision_key="b" * 64,
    )


def executor(repo, exchange, info) -> HyperliquidTestnetExecutionService:
    return HyperliquidTestnetExecutionService(
        repo,
        TEST_KEY,
        MASTER_ADDRESS,
        max_open_notional_usd=25,
        slippage_bps=50,
        exchange=exchange,
        info=info,
    )


def test_testnet_settings_require_confirmation_credentials_and_postgres() -> None:
    with pytest.raises(ValidationError, match="TESTNET_CONFIRMATION"):
        Settings(_env_file=None, agent_mode="testnet")
    with pytest.raises(ValidationError, match="PostgreSQL"):
        Settings(
            _env_file=None,
            agent_mode="testnet",
            testnet_confirmation="I_UNDERSTAND_TESTNET",
            hyperliquid_account_address=MASTER_ADDRESS,
            hyperliquid_private_key=TEST_KEY,
            database_url="sqlite://",
        )
    configured = Settings(
        _env_file=None,
        agent_mode="testnet",
        testnet_confirmation="I_UNDERSTAND_TESTNET",
        hyperliquid_account_address=MASTER_ADDRESS,
        hyperliquid_private_key=TEST_KEY,
        database_url="postgresql+psycopg://localhost/agent",
    )
    assert configured.hyperliquid_execution_network == "testnet"


def test_readiness_accepts_authorized_api_wallet_but_blocks_master_key() -> None:
    ready = HyperliquidTestnetReadiness(
        TEST_KEY,
        MASTER_ADDRESS,
        info=FakeInfo(account_value=100),
    ).inspect()
    assert ready["authorized"] is True
    assert ready["dedicated_api_wallet"] is True
    assert ready["ready_for_orders"] is True

    signer = Account.from_key(TEST_KEY).address
    blocked = HyperliquidTestnetReadiness(
        TEST_KEY,
        signer,
        info=FakeInfo(account_value=0),
    ).inspect()
    assert blocked["ready_for_orders"] is False
    assert "MASTER_KEY_FORBIDDEN_FOR_AUTOMATION" in blocked["blockers"]
    assert "TESTNET_COLLATERAL_UNAVAILABLE" in blocked["blockers"]


def test_readiness_uses_spot_collateral_for_unified_accounts() -> None:
    ready = HyperliquidTestnetReadiness(
        TEST_KEY,
        MASTER_ADDRESS,
        network="mainnet",
        info=FakeInfo(account_value=99.8, abstraction="unifiedAccount"),
    ).inspect()
    assert ready["network"] == "mainnet"
    assert ready["account_abstraction"] == "unifiedAccount"
    assert ready["collateral_source"] == "spotClearinghouseState"
    assert ready["available_collateral_usd"] == 99.8
    assert ready["ready_for_orders"] is True


def test_filled_order_is_durable_and_duplicate_is_not_resubmitted() -> None:
    repo = repository()
    info = FakeInfo()
    exchange = FakeExchange(info)
    service = executor(repo, exchange, info)

    first = service.execute(order())
    duplicate = service.execute(order())

    assert first.status == "FILLED"
    assert duplicate.duplicate_prevented is True
    assert duplicate.cloid == first.cloid
    assert len(exchange.bulk_calls) == 2
    assert exchange.leverage_calls == [(4, "BTC", False)]
    requests, grouping = exchange.bulk_calls[0]
    assert grouping == "normalTpsl"
    assert requests[0]["coin"] == "BTC"
    assert requests[0]["is_buy"] is True
    assert requests[0]["reduce_only"] is False
    assert str(requests[0]["cloid"]) == first.cloid
    assert requests[1]["order_type"]["trigger"]["tpsl"] == "tp"
    assert requests[2]["order_type"]["trigger"]["tpsl"] == "sl"
    assert exchange.bulk_calls[1][1] == "na"
    protections = repo.protective_orders(symbol="BTC")
    assert [item["kind"] for item in protections].count("SL") == 1
    assert [item["kind"] for item in protections].count("TP") == 3
    assert all(item["status"] == "ACTIVE" for item in protections)


def test_lost_ack_is_never_retried_and_is_reconciled_by_cloid() -> None:
    repo = repository()
    info = FakeInfo(order_status="filled")
    exchange = FakeExchange(info, error=TimeoutError("lost ACK"))
    service = executor(repo, exchange, info)

    uncertain = service.execute(order())
    duplicate = service.execute(order())
    reconciled = service.reconcile()

    assert uncertain.status == "UNKNOWN"
    assert duplicate.duplicate_prevented is True
    assert len(exchange.bulk_calls) == 1
    assert exchange.leverage_calls == [(4, "BTC", False)]
    assert reconciled[0].status == "FILLED"
    assert info.queries[0][1] == uncertain.cloid
    assert repo.unresolved_intents() == []


def test_mainnet_first_cycle_submits_only_one_open_order() -> None:
    repo = repository()
    info = FakeInfo()
    exchange = FakeExchange(info)
    service = HyperliquidTestnetExecutionService(
        repo,
        TEST_KEY,
        MASTER_ADDRESS,
        max_open_notional_usd=20,
        max_open_orders_per_cycle=1,
        slippage_bps=30,
        network="mainnet",
        exchange=exchange,
        info=info,
    )
    first_order = order().model_copy(update={"notional_usd": 20})
    second_order = first_order.model_copy(
        update={
            "symbol": "ETH",
            "mark_px": 3_400,
            "invalidation_px": 3_300,
            "targets": [3_500, 3_600],
            "decision_key": "c" * 64,
        }
    )

    first = service.execute(first_order)
    second = service.execute(second_order)

    assert first.status == "FILLED"
    assert second.status == "REJECTED"
    assert len(exchange.bulk_calls) == 2


def test_live_executor_has_no_application_notional_cap() -> None:
    repo = repository()
    info = FakeInfo()
    exchange = FakeExchange(info)
    service = HyperliquidTestnetExecutionService(
        repo,
        TEST_KEY,
        MASTER_ADDRESS,
        max_open_notional_usd=None,
        max_open_orders_per_cycle=100,
        slippage_bps=30,
        network="mainnet",
        exchange=exchange,
        info=info,
    )
    uncapped = order().model_copy(update={"notional_usd": 500})

    result = service.execute(uncapped)

    assert result.status == "FILLED"
    assert exchange.bulk_calls


def test_partial_fill_scales_remaining_take_profits_to_filled_size() -> None:
    repo = repository()
    info = FakeInfo()
    response = {
        "status": "ok",
        "response": {
            "data": {
                "statuses": [
                    {"filled": {"oid": 42, "totalSz": "0.00025"}},
                    {"resting": {"oid": 43}},
                    {"resting": {"oid": 44}},
                ]
            }
        },
    }
    exchange = FakeExchange(info, response=response)
    service = executor(repo, exchange, info)

    result = service.execute(order())

    assert result.status == "FILLED"
    primary_tp_size = exchange.bulk_calls[0][0][1]["sz"]
    remaining_sizes = [item["sz"] for item in exchange.bulk_calls[1][0]]
    assert primary_tp_size == 0.00019
    assert round(sum(remaining_sizes), 5) == 0.00006
    assert round(primary_tp_size + sum(remaining_sizes), 5) == 0.00025


@pytest.mark.parametrize(
    ("exchange_status", "expected"),
    [
        ("open", "OPEN"),
        ("filled", "FILLED"),
        ("siblingFilledCanceled", "CANCELED"),
        ("reduceOnlyCanceled", "CANCELED"),
        ("rejected", "REJECTED"),
    ],
)
def test_reconciled_status_reads_nested_exchange_payload(
    exchange_status: str,
    expected: str,
) -> None:
    response = {
        "status": "order",
        "order": {
            "status": exchange_status,
            "order": {"coin": "BTC"},
        },
    }

    assert HyperliquidTestnetExecutionService._reconciled_status(response) == expected


def test_reconcile_archives_local_protections_when_position_and_orders_are_absent() -> None:
    repo = repository()
    info = FakeInfo()
    exchange = FakeExchange(info)
    service = executor(repo, exchange, info)
    service.execute(order())

    for protection in repo.protective_orders(symbol="BTC"):
        info.order_responses[protection["cloid"]] = {"status": "unknownOid"}
    info.frontend_orders = []
    info.asset_positions = []

    service.reconcile()

    protections = repo.protective_orders(symbol="BTC")
    assert protections
    assert all(item["status"] == "CANCELED" for item in protections)
    assert exchange.cancel_calls == []


def test_cancel_ack_without_verification_never_claims_protections_are_canceled() -> None:
    repo = repository()
    info = FakeInfo()
    exchange = FakeExchange(info)
    service = executor(repo, exchange, info)
    service.execute(order())

    protections = repo.protective_orders(symbol="BTC")
    info.frontend_orders = [
        {"coin": "BTC", "cloid": item["cloid"]}
        for item in protections
    ]
    info.frontend_orders_fail_after = 1
    info.asset_positions = []

    service.reconcile()

    assert len(exchange.cancel_calls) == 1
    assert all(
        item["status"] == "UNKNOWN"
        for item in repo.protective_orders(symbol="BTC")
    )


@pytest.mark.parametrize(
    ("direction", "signed_size", "expected_is_buy"),
    [("LONG", "0.00015", False), ("SHORT", "-0.00015", True)],
)
def test_reconcile_rearms_missing_stop_once_for_remaining_position(
    direction: str,
    signed_size: str,
    expected_is_buy: bool,
) -> None:
    repo = repository()
    info = FakeInfo()
    exchange = FakeExchange(info)
    service = executor(repo, exchange, info)
    opening = order()
    if direction == "SHORT":
        opening = opening.model_copy(
            update={
                "direction": "SHORT",
                "invalidation_px": 66_500,
                "targets": [64_000, 63_000, 62_000],
            }
        )
    service.execute(opening)

    initial = repo.protective_orders(symbol="BTC")
    old_stop = next(item for item in initial if item["kind"] == "SL")
    take_profits = [item for item in initial if item["kind"] == "TP"]
    info.asset_positions = [
        {
            "position": {
                "coin": "BTC",
                "szi": signed_size,
                "entryPx": "65000",
                "positionValue": "9.75",
            }
        }
    ]
    info.frontend_orders = [
        {"coin": "BTC", "cloid": item["cloid"]}
        for item in take_profits
    ]
    info.order_responses[old_stop["cloid"]] = {
        "status": "order",
        "order": {
            "status": "siblingFilledCanceled",
            "order": {"coin": "BTC"},
        },
    }
    exchange.response = {
        "status": "ok",
        "response": {"data": {"statuses": [{"resting": {"oid": 99}}]}},
    }

    service.reconcile()

    stops = [
        item for item in repo.protective_orders(symbol="BTC")
        if item["kind"] == "SL"
    ]
    assert len(stops) == 2
    assert next(item for item in stops if item["protection_id"] == old_stop["protection_id"])[
        "status"
    ] == "CANCELED"
    rearmed = next(item for item in stops if item["protection_id"] != old_stop["protection_id"])
    assert rearmed["status"] == "ACTIVE"
    assert len(exchange.calls) == 1
    _, request = exchange.calls[0]
    assert request["coin"] == "BTC"
    assert request["is_buy"] is expected_is_buy
    assert request["reduce_only"] is True
    assert request["sz"] == 0.00015

    info.frontend_orders.append({"coin": "BTC", "cloid": rearmed["cloid"]})
    info.order_responses[rearmed["cloid"]] = {
        "status": "order",
        "order": {"status": "open", "order": {"coin": "BTC"}},
    }
    service.reconcile()

    assert len(exchange.calls) == 1
    assert len(
        [
            item for item in repo.protective_orders(symbol="BTC")
            if item["kind"] == "SL"
        ]
    ) == 2

    info.frontend_orders = [
        item
        for item in info.frontend_orders
        if item["cloid"] != rearmed["cloid"]
    ]
    info.order_responses[rearmed["cloid"]] = {
        "status": "order",
        "order": {
            "status": "reduceOnlyCanceled",
            "order": {"coin": "BTC"},
        },
    }
    service.reconcile()

    stops = [
        item for item in repo.protective_orders(symbol="BTC")
        if item["kind"] == "SL"
    ]
    assert len(exchange.calls) == 2
    assert len(stops) == 3
    second_rearm = next(
        item
        for item in stops
        if item["protection_id"]
        not in {old_stop["protection_id"], rearmed["protection_id"]}
    )
    assert second_rearm["status"] == "ACTIVE"

    info.frontend_orders.append(
        {"coin": "BTC", "cloid": second_rearm["cloid"]}
    )
    service.reconcile()

    assert len(exchange.calls) == 2


def test_stop_rearm_unknown_ack_is_never_resubmitted() -> None:
    repo = repository()
    info = FakeInfo()
    exchange = FakeExchange(info)
    service = executor(repo, exchange, info)
    service.execute(order())

    initial = repo.protective_orders(symbol="BTC")
    old_stop = next(item for item in initial if item["kind"] == "SL")
    info.asset_positions = [
        {
            "position": {
                "coin": "BTC",
                "szi": "0.00015",
                "entryPx": "65000",
                "positionValue": "9.75",
            }
        }
    ]
    info.frontend_orders = [
        {"coin": "BTC", "cloid": item["cloid"]}
        for item in initial
        if item["kind"] == "TP"
    ]
    info.order_responses[old_stop["cloid"]] = {
        "status": "order",
        "order": {
            "status": "siblingFilledCanceled",
            "order": {"coin": "BTC"},
        },
    }
    exchange.error = TimeoutError("lost stop ACK")

    service.reconcile()

    rearmed = next(
        item
        for item in repo.protective_orders(symbol="BTC")
        if item["kind"] == "SL" and item["protection_id"] != old_stop["protection_id"]
    )
    assert rearmed["status"] == "UNKNOWN"
    assert len(exchange.calls) == 1

    exchange.error = None
    info.order_responses[rearmed["cloid"]] = {"status": "unknownOid"}
    service.reconcile()

    assert len(exchange.calls) == 1
