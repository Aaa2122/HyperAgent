from __future__ import annotations

"""Pure reconstruction of closed Hyperliquid position round trips.

The exchange remains the source of truth for fills and realised PnL.  Local
intents, protections and cycles are only used to enrich those fills with the
agent's leverage, thesis and close reason.  Nothing in this module performs
I/O, which makes rebuilding the view safe and idempotent.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
from typing import Any, Iterable, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field


_EPSILON = 1e-10


class TradeRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    trade_id: str
    symbol: str
    side: Literal["LONG", "SHORT"]
    opened_at: datetime
    closed_at: datetime
    avg_entry_px: float = Field(gt=0)
    avg_exit_px: float = Field(gt=0)
    initial_size: float = Field(gt=0)
    initial_notional_usd: float = Field(gt=0)
    leverage: int = Field(default=1, ge=1)
    gross_pnl_usd: float
    fees_usd: float
    funding_usd: float
    funding_source: Literal["hyperliquid_user_funding", "unavailable"]
    net_pnl_usd: float
    price_return_pct: float
    margin_return_pct: float
    close_reason: str
    outcome: Literal["PROFIT", "LOSS", "BREAK_EVEN"]
    thesis: str | None = None
    rationale: str | None = None
    source: str


class TradeMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    total_trades: int = Field(ge=0)
    win_rate_pct: float = Field(ge=0, le=100)
    avg_win_usd: float
    avg_loss_usd: float
    profit_factor: float | None
    cumulative_net_pnl_usd: float
    max_drawdown_usd: float = Field(ge=0)
    max_drawdown_pct: float = Field(ge=0)


@dataclass(frozen=True)
class _OpenMetadata:
    leverage: int = 1
    thesis: str | None = None
    rationale: str | None = None
    source: str = "hyperliquid_user_fills"


@dataclass
class _RoundTrip:
    symbol: str
    side: Literal["LONG", "SHORT"]
    opened_at: datetime
    remaining_size: float
    remaining_entry_notional: float
    entry_size: float
    entry_notional: float
    fees: float
    leverage: int
    thesis: str | None
    rationale: str | None
    source: str
    exit_size: float = 0.0
    exit_notional: float = 0.0
    gross_pnl: float = 0.0
    close_reason: str = "UNKNOWN"
    fill_keys: list[str] = field(default_factory=list)

    @property
    def avg_entry_px(self) -> float:
        return self.entry_notional / self.entry_size


@dataclass(frozen=True)
class _LocalContext:
    intents_by_cloid: dict[str, Mapping[str, Any]]
    protections_by_cloid: dict[str, Mapping[str, Any]]
    cycles_by_id: dict[str, Mapping[str, Any]]

    @classmethod
    def build(
        cls,
        intents: Iterable[Mapping[str, Any]],
        protections: Iterable[Mapping[str, Any]],
        cycles: Iterable[Mapping[str, Any]],
    ) -> "_LocalContext":
        return cls(
            intents_by_cloid={
                cloid: item
                for item in intents
                if (cloid := _cloid(item.get("cloid")))
            },
            protections_by_cloid={
                cloid: item
                for item in protections
                if (cloid := _cloid(item.get("cloid")))
            },
            cycles_by_id={
                str(item.get("cycle_id")): item
                for item in cycles
                if item.get("cycle_id")
            },
        )

    def opening_metadata(
        self, fill: Mapping[str, Any], symbol: str
    ) -> _OpenMetadata:
        intent = self.intents_by_cloid.get(_cloid(fill.get("cloid")))
        if not intent or str(intent.get("action", "")).upper() != "OPEN":
            return _OpenMetadata()

        payload = _mapping(intent.get("payload"))
        leverage = max(1, int(_number(payload.get("leverage"), 1)))
        thesis = _optional_text(payload.get("thesis"))
        rationale = _optional_text(payload.get("rationale"))
        cycle_id = str(intent.get("cycle_id") or "")
        cycle = self.cycles_by_id.get(cycle_id)
        if cycle:
            cycle_thesis, cycle_rationale = _cycle_explanation(cycle, symbol)
            thesis = thesis or cycle_thesis
            rationale = rationale or cycle_rationale
        return _OpenMetadata(
            leverage=leverage,
            thesis=thesis,
            rationale=rationale,
            source=(
                f"hyperliquid_user_fills+local_cycle:{cycle_id}"
                if cycle_id
                else "hyperliquid_user_fills+local_intent"
            ),
        )

    def close_reason(self, fill: Mapping[str, Any], symbol: str) -> str:
        direction = str(fill.get("dir") or "")
        if "liquidat" in direction.casefold():
            return "LIQUIDATION"

        cloid = _cloid(fill.get("cloid"))
        protection = self.protections_by_cloid.get(cloid)
        if protection:
            kind = str(protection.get("kind") or "").upper()
            if kind in {"TP", "SL"}:
                return kind

        intent = self.intents_by_cloid.get(cloid)
        if intent:
            payload = _mapping(intent.get("payload"))
            explicit = payload.get("close_reason") or payload.get("reason")
            if explicit:
                return _canonical_close_reason(str(explicit), local=True)
            cycle = self.cycles_by_id.get(str(intent.get("cycle_id") or ""))
            _, rationale = _cycle_explanation(cycle or {}, symbol)
            if rationale:
                return _canonical_close_reason(rationale, local=True)
            action = str(intent.get("action") or "").upper()
            if action in {"CLOSE", "REDUCE"}:
                return "AGENT_DECISION"

        # This agent always assigns CLOIDs.  A close without matching local
        # context is therefore an exchange/UI/manual action, not an agent TP/SL.
        return "MANUAL" if not cloid else "UNKNOWN"


def reconstruct_closed_trades(
    user_fills: Sequence[Mapping[str, Any]],
    *,
    funding_records: Sequence[Mapping[str, Any]] | None = None,
    intents: Sequence[Mapping[str, Any]] = (),
    protections: Sequence[Mapping[str, Any]] = (),
    cycles: Sequence[Mapping[str, Any]] = (),
) -> list[TradeRecord]:
    """Rebuild complete flat-to-flat position lifecycles from exchange fills.

    Partial exits and direct reversals are supported.  Incomplete positions and
    orphan closing fills (for example when the exchange retention window begins
    mid-trade) are deliberately omitted rather than reported with invented
    entry data.
    """

    context = _LocalContext.build(intents, protections, cycles)
    fills = _normalised_fills(user_fills)
    open_by_symbol: dict[str, _RoundTrip] = {}
    completed: list[TradeRecord] = []

    for fill_key, fill in fills:
        symbol = str(fill.get("coin") or fill.get("symbol") or "").strip()
        if not symbol:
            continue
        key = symbol.casefold()
        size = _number(fill.get("sz") or fill.get("size"))
        price = _number(fill.get("px") or fill.get("price"))
        timestamp = _timestamp(fill.get("time") or fill.get("timestamp"))
        flow_side = _flow_side(fill)
        if size <= 0 or price <= 0 or timestamp is None or flow_side is None:
            continue
        fee = _number(fill.get("fee"))
        current = open_by_symbol.get(key)

        if current is None:
            if not _is_complete_open_start(fill):
                continue
            metadata = context.opening_metadata(fill, symbol)
            open_by_symbol[key] = _new_round_trip(
                symbol,
                flow_side,
                timestamp,
                size,
                price,
                fee,
                metadata,
                f"{fill_key}:open:{size:.12g}",
            )
            continue

        if current.side == flow_side:
            current.remaining_size += size
            current.remaining_entry_notional += size * price
            current.entry_size += size
            current.entry_notional += size * price
            current.fees += fee
            current.fill_keys.append(f"{fill_key}:open:{size:.12g}")
            metadata = context.opening_metadata(fill, symbol)
            if current.source == "hyperliquid_user_fills" and (
                metadata.source != "hyperliquid_user_fills"
            ):
                current.leverage = metadata.leverage
                current.thesis = metadata.thesis
                current.rationale = metadata.rationale
                current.source = metadata.source
            continue

        close_size = min(current.remaining_size, size)
        fee_fraction = close_size / size
        position_entry_px = current.remaining_entry_notional / current.remaining_size
        current.exit_size += close_size
        current.exit_notional += close_size * price
        current.fees += fee * fee_fraction
        current.gross_pnl += _fill_gross_pnl(
            fill, current.side, position_entry_px, close_size, price
        )
        current.remaining_entry_notional -= position_entry_px * close_size
        current.remaining_size -= close_size
        current.close_reason = context.close_reason(fill, symbol)
        current.fill_keys.append(f"{fill_key}:close:{close_size:.12g}")

        if current.remaining_size <= _EPSILON:
            completed.append(
                _finalize_trade(
                    current,
                    timestamp,
                    funding_records=funding_records,
                )
            )
            del open_by_symbol[key]

        remainder = size - close_size
        if remainder > _EPSILON:
            metadata = context.opening_metadata(fill, symbol)
            open_by_symbol[key] = _new_round_trip(
                symbol,
                flow_side,
                timestamp,
                remainder,
                price,
                fee * (remainder / size),
                metadata,
                f"{fill_key}:reverse-open:{remainder:.12g}",
            )

    return sorted(completed, key=lambda item: item.closed_at, reverse=True)


def calculate_trade_metrics(trades: Sequence[TradeRecord | Mapping[str, Any]]) -> TradeMetrics:
    """Calculate metrics over closed trades only.

    ``max_drawdown_pct`` is the peak-to-trough decline of the cumulative margin
    return series.  It remains meaningful without pretending that deployed
    margin is the account's starting equity, which is not present in user fills.
    """

    records = [
        item if isinstance(item, TradeRecord) else TradeRecord.model_validate(item)
        for item in trades
    ]
    ordered = sorted(records, key=lambda item: (item.closed_at, item.trade_id))
    wins = [item.net_pnl_usd for item in ordered if item.net_pnl_usd > _EPSILON]
    losses = [item.net_pnl_usd for item in ordered if item.net_pnl_usd < -_EPSILON]
    total = len(ordered)
    gross_wins = sum(wins)
    gross_losses = abs(sum(losses))

    pnl_curve = 0.0
    pnl_peak = 0.0
    return_curve = 0.0
    return_peak = 0.0
    max_drawdown = 0.0
    max_drawdown_pct = 0.0
    for item in ordered:
        pnl_curve += item.net_pnl_usd
        pnl_peak = max(pnl_peak, pnl_curve)
        max_drawdown = max(max_drawdown, pnl_peak - pnl_curve)

        return_curve += item.margin_return_pct
        return_peak = max(return_peak, return_curve)
        max_drawdown_pct = max(max_drawdown_pct, return_peak - return_curve)

    return TradeMetrics(
        total_trades=total,
        win_rate_pct=(len(wins) / total * 100 if total else 0.0),
        avg_win_usd=(gross_wins / len(wins) if wins else 0.0),
        avg_loss_usd=(sum(losses) / len(losses) if losses else 0.0),
        profit_factor=(gross_wins / gross_losses if gross_losses else None),
        cumulative_net_pnl_usd=sum(item.net_pnl_usd for item in ordered),
        max_drawdown_usd=max_drawdown,
        max_drawdown_pct=max_drawdown_pct,
    )


def _new_round_trip(
    symbol: str,
    side: Literal["LONG", "SHORT"],
    timestamp: datetime,
    size: float,
    price: float,
    fee: float,
    metadata: _OpenMetadata,
    fill_key: str,
) -> _RoundTrip:
    return _RoundTrip(
        symbol=symbol,
        side=side,
        opened_at=timestamp,
        remaining_size=size,
        remaining_entry_notional=size * price,
        entry_size=size,
        entry_notional=size * price,
        fees=fee,
        leverage=metadata.leverage,
        thesis=metadata.thesis,
        rationale=metadata.rationale,
        source=metadata.source,
        fill_keys=[fill_key],
    )


def _finalize_trade(
    trade: _RoundTrip,
    closed_at: datetime,
    *,
    funding_records: Sequence[Mapping[str, Any]] | None,
) -> TradeRecord:
    avg_entry = trade.avg_entry_px
    avg_exit = trade.exit_notional / trade.exit_size
    direction = 1 if trade.side == "LONG" else -1
    funding = _trade_funding(
        funding_records or (), trade.symbol, trade.opened_at, closed_at
    )
    funding_source: Literal["hyperliquid_user_funding", "unavailable"] = (
        "hyperliquid_user_funding"
        if funding_records is not None
        else "unavailable"
    )
    net_pnl = trade.gross_pnl - trade.fees + funding
    initial_margin = trade.entry_notional / trade.leverage
    outcome: Literal["PROFIT", "LOSS", "BREAK_EVEN"] = (
        "PROFIT"
        if net_pnl > _EPSILON
        else "LOSS"
        if net_pnl < -_EPSILON
        else "BREAK_EVEN"
    )
    identity = "|".join(
        [trade.symbol.casefold(), trade.side, *trade.fill_keys]
    ).encode("utf-8")
    trade_id = "rt_" + hashlib.sha256(identity).hexdigest()[:24]
    return TradeRecord(
        trade_id=trade_id,
        symbol=trade.symbol,
        side=trade.side,
        opened_at=trade.opened_at,
        closed_at=closed_at,
        avg_entry_px=avg_entry,
        avg_exit_px=avg_exit,
        initial_size=trade.entry_size,
        initial_notional_usd=trade.entry_notional,
        leverage=trade.leverage,
        gross_pnl_usd=_clean(trade.gross_pnl),
        fees_usd=_clean(trade.fees),
        funding_usd=_clean(funding),
        funding_source=funding_source,
        net_pnl_usd=_clean(net_pnl),
        price_return_pct=_clean(direction * (avg_exit / avg_entry - 1) * 100),
        margin_return_pct=_clean(net_pnl / initial_margin * 100),
        close_reason=trade.close_reason,
        outcome=outcome,
        thesis=trade.thesis,
        rationale=trade.rationale,
        source=trade.source,
    )


def _normalised_fills(
    fills: Sequence[Mapping[str, Any]],
) -> list[tuple[str, Mapping[str, Any]]]:
    unique: dict[str, Mapping[str, Any]] = {}
    for fill in fills:
        identity = _fill_identity(fill)
        # Exact repeated exchange records must not double the reconstructed PnL.
        unique.setdefault(identity, fill)
    return sorted(
        unique.items(),
        key=lambda item: (
            _timestamp(item[1].get("time") or item[1].get("timestamp"))
            or datetime.min.replace(tzinfo=timezone.utc),
            _sortable_identifier(item[1].get("tid")),
            _sortable_identifier(item[1].get("oid")),
            item[0],
        ),
    )


def _fill_identity(fill: Mapping[str, Any]) -> str:
    symbol = str(fill.get("coin") or fill.get("symbol") or "").casefold()
    tid = fill.get("tid")
    if tid not in (None, ""):
        return f"tid:{symbol}:{tid}"
    exchange_hash = fill.get("hash")
    oid = fill.get("oid")
    if exchange_hash or oid not in (None, ""):
        return (
            f"order:{symbol}:{exchange_hash or ''}:{oid or ''}:"
            f"{fill.get('time')}:{fill.get('dir')}:{fill.get('px')}:{fill.get('sz')}:"
            f"{fill.get('closedPnl')}:{fill.get('fee')}"
        )
    # Hyperliquid normally supplies ``tid``.  For defensive fixtures/legacy
    # records, an exact payload fingerprint is the only safe idempotency key.
    return (
        f"anonymous:{symbol}:{fill.get('time')}:{fill.get('dir')}:"
        f"{fill.get('side')}:{fill.get('px')}:{fill.get('sz')}:"
        f"{fill.get('closedPnl')}:{fill.get('fee')}:{fill.get('cloid')}:"
        f"{fill.get('startPosition')}"
    )


def _fill_gross_pnl(
    fill: Mapping[str, Any],
    side: Literal["LONG", "SHORT"],
    position_entry_px: float,
    close_size: float,
    exit_price: float,
) -> float:
    reported = fill.get("closedPnl")
    if reported not in (None, ""):
        return _number(reported)
    direction = 1 if side == "LONG" else -1
    return direction * (exit_price - position_entry_px) * close_size


def _trade_funding(
    records: Sequence[Mapping[str, Any]],
    symbol: str,
    opened_at: datetime,
    closed_at: datetime,
) -> float:
    total = 0.0
    for record in records:
        delta = _mapping(record.get("delta"))
        coin = str(delta.get("coin") or record.get("coin") or "")
        timestamp = _timestamp(record.get("time") or record.get("timestamp"))
        if coin.casefold() != symbol.casefold() or timestamp is None:
            continue
        # Half-open intervals prevent double attribution on a direct reversal.
        if opened_at <= timestamp < closed_at:
            total += _number(delta.get("usdc", record.get("usdc")))
    return total


def _flow_side(fill: Mapping[str, Any]) -> Literal["LONG", "SHORT"] | None:
    side = str(fill.get("side") or "").strip().upper()
    if side in {"B", "BUY", "BID"}:
        return "LONG"
    if side in {"A", "S", "SELL", "ASK"}:
        return "SHORT"
    direction = str(fill.get("dir") or "").casefold()
    if "open long" in direction or "close short" in direction:
        return "LONG"
    if "open short" in direction or "close long" in direction:
        return "SHORT"
    return None


def _is_complete_open_start(fill: Mapping[str, Any]) -> bool:
    direction = str(fill.get("dir") or "").casefold()
    if "close" in direction or "liquidat" in direction:
        return False
    start_position = fill.get("startPosition")
    if start_position not in (None, "") and abs(_number(start_position)) > _EPSILON:
        return False
    return True


def _cycle_explanation(
    cycle: Mapping[str, Any], symbol: str
) -> tuple[str | None, str | None]:
    state = _mapping(cycle.get("state"))
    decision = _mapping(state.get("decision"))
    playbook = _mapping(decision.get("playbook"))
    playbook_payload = _mapping(playbook.get("payload"))
    plans = playbook_payload.get("plans") or ()
    thesis = next(
        (
            _optional_text(_mapping(item).get("thesis"))
            for item in plans
            if str(_mapping(item).get("symbol") or "").casefold()
            == symbol.casefold()
        ),
        None,
    )
    trader = _mapping(decision.get("trader"))
    decisions = trader.get("decisions") or ()
    rationale = next(
        (
            _optional_text(_mapping(item).get("rationale"))
            for item in decisions
            if str(_mapping(item).get("symbol") or "").casefold()
            == symbol.casefold()
        ),
        None,
    )
    return thesis, rationale


def _canonical_close_reason(value: str, *, local: bool) -> str:
    normalized = " ".join(value.replace("_", " ").replace("-", " ").split()).casefold()
    if normalized in {"tp", "take profit"} or "take profit" in normalized or "target" in normalized:
        return "TP"
    if normalized in {"sl", "stop loss"} or "stop loss" in normalized or "invalidation" in normalized:
        return "SL"
    if "time stop" in normalized or "timeout" in normalized or "horizon" in normalized:
        return "TIME_STOP"
    if "liquidat" in normalized:
        return "LIQUIDATION"
    if "manual" in normalized:
        return "MANUAL"
    return "AGENT_DECISION" if local else "UNKNOWN"


def _timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, (int, float)):
        seconds = float(value) / 1000 if abs(float(value)) >= 10_000_000_000 else float(value)
        try:
            result = datetime.fromtimestamp(seconds, timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    elif isinstance(value, str) and value.strip():
        stripped = value.strip()
        try:
            numeric = float(stripped)
        except ValueError:
            try:
                result = datetime.fromisoformat(stripped.replace("Z", "+00:00"))
            except ValueError:
                return None
        else:
            return _timestamp(numeric)
    else:
        return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _cloid(value: Any) -> str:
    return str(value or "").strip().casefold()


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _sortable_identifier(value: Any) -> tuple[int, int | str]:
    try:
        return 0, int(value)
    except (TypeError, ValueError):
        return 1, str(value or "")


def _clean(value: float) -> float:
    return 0.0 if abs(value) <= _EPSILON else value
