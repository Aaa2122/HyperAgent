from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from enum import Enum
from typing import Any, Iterable, Protocol
from zoneinfo import ZoneInfo


XYZ_US_EQUITY_SYMBOLS = (
    "TSLA",
    "NVDA",
    "AAPL",
    "MSFT",
    "AMZN",
    "META",
    "GOOGL",
)

US_EQUITY_TIMEZONE = "America/New_York"


class AssetClass(str, Enum):
    US_EQUITY = "us_equity"


class InstrumentKind(str, Enum):
    HIP3_PERPETUAL = "hip3_perpetual"


class ExecutionScope(str, Enum):
    """The only scope exposed by this registry.

    LIVE is deliberately not an enum member: discovering an instrument must never
    make it eligible for a signed exchange action.
    """

    READ_ONLY_PAPER = "read_only_paper"


class DexDiscoveryStatus(str, Enum):
    DISCOVERED = "discovered"
    NOT_DISCOVERED = "not_discovered"
    DATA_UNAVAILABLE = "data_unavailable"


class VenueMarketStatus(str, Enum):
    AVAILABLE = "available"
    HALTED = "halted"
    DELISTED = "delisted"
    DATA_UNAVAILABLE = "data_unavailable"
    NOT_LISTED = "not_listed"


class UsEquitySessionStatus(str, Enum):
    PRE_MARKET = "pre_market"
    REGULAR = "regular"
    AFTER_HOURS = "after_hours"
    CLOSED = "closed"


class ReadOnlyInfoClient(Protocol):
    """Smallest client surface required by HIP-3 discovery: POST /info only."""

    def post(self, payload: dict[str, Any]) -> Any: ...


@dataclass(frozen=True)
class UsEquitySession:
    status: UsEquitySessionStatus
    as_of: datetime
    local_time: datetime
    timezone: str = US_EQUITY_TIMEZONE
    calendar: str = "weekday_clock_no_holidays"
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "as_of": self.as_of.isoformat(),
            "local_time": self.local_time.isoformat(),
            "timezone": self.timezone,
            "calendar": self.calendar,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class VenueDescriptor:
    name: str
    status: DexDiscoveryStatus
    status_reason: str
    full_name: str | None = None
    deployer: str | None = None
    total_net_deposit: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status.value,
            "status_reason": self.status_reason,
            "full_name": self.full_name,
            "deployer": self.deployer,
            "total_net_deposit": self.total_net_deposit,
        }


@dataclass(frozen=True)
class Instrument:
    instrument_id: str
    symbol: str
    venue_symbol: str
    venue: str
    venue_status: VenueMarketStatus
    venue_status_reason: str
    session_status: UsEquitySessionStatus
    session_timezone: str
    mark_px: float | None = None
    mid_px: float | None = None
    oracle_px: float | None = None
    previous_day_px: float | None = None
    day_notional_volume_usd: float | None = None
    open_interest: float | None = None
    funding_rate: float | None = None
    size_decimals: int | None = None
    max_leverage: int | None = None
    only_isolated: bool | None = None
    universe_index: int | None = None
    asset_class: AssetClass = field(default=AssetClass.US_EQUITY, init=False)
    kind: InstrumentKind = field(default=InstrumentKind.HIP3_PERPETUAL, init=False)
    execution_scope: ExecutionScope = field(
        default=ExecutionScope.READ_ONLY_PAPER, init=False
    )
    read_only: bool = field(default=True, init=False)
    live_eligible: bool = field(default=False, init=False)

    @property
    def paper_eligible(self) -> bool:
        # The reference-equity session is informational. HIP-3 venue availability
        # alone determines whether a deterministic PAPER consumer may use the mark.
        return self.venue_status is VenueMarketStatus.AVAILABLE

    def to_dict(self) -> dict[str, Any]:
        return {
            "instrument_id": self.instrument_id,
            "symbol": self.symbol,
            "venue_symbol": self.venue_symbol,
            "venue": self.venue,
            "asset_class": self.asset_class.value,
            "kind": self.kind.value,
            "execution_scope": self.execution_scope.value,
            "read_only": self.read_only,
            "paper_eligible": self.paper_eligible,
            "live_eligible": self.live_eligible,
            "venue_status": self.venue_status.value,
            "venue_status_reason": self.venue_status_reason,
            "session_status": self.session_status.value,
            "session_timezone": self.session_timezone,
            "mark_px": self.mark_px,
            "mid_px": self.mid_px,
            "oracle_px": self.oracle_px,
            "previous_day_px": self.previous_day_px,
            "day_notional_volume_usd": self.day_notional_volume_usd,
            "open_interest": self.open_interest,
            "funding_rate": self.funding_rate,
            "size_decimals": self.size_decimals,
            "max_leverage": self.max_leverage,
            "only_isolated": self.only_isolated,
            "universe_index": self.universe_index,
        }


@dataclass(frozen=True)
class InstrumentRegistrySnapshot:
    as_of: datetime
    venue: VenueDescriptor
    session: UsEquitySession
    instruments: tuple[Instrument, ...]
    warnings: tuple[str, ...] = ()
    execution_scope: ExecutionScope = field(
        default=ExecutionScope.READ_ONLY_PAPER, init=False
    )

    def get(self, symbol: str) -> Instrument | None:
        normalized = symbol.strip().upper()
        return next(
            (item for item in self.instruments if item.symbol == normalized), None
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "as_of": self.as_of.isoformat(),
            "execution_scope": self.execution_scope.value,
            "venue": self.venue.to_dict(),
            "session": self.session.to_dict(),
            "instruments": [item.to_dict() for item in self.instruments],
            "warnings": list(self.warnings),
        }


class UsEquitySessionClock:
    """Indicative US equity clock with DST, weekends, and injectable holidays.

    This intentionally does not claim to be an exchange calendar. Consumers can
    pass known closure dates, while the public contract remains honest about the
    default weekday-only calculation.
    """

    def __init__(self, holidays: Iterable[date] = ()) -> None:
        self.holidays = frozenset(holidays)
        self.timezone = ZoneInfo(US_EQUITY_TIMEZONE)

    def at(self, as_of: datetime) -> UsEquitySession:
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        normalized_as_of = as_of.astimezone(timezone.utc)
        local = normalized_as_of.astimezone(self.timezone)
        local_clock = local.timetz().replace(tzinfo=None)

        if local.weekday() >= 5:
            status = UsEquitySessionStatus.CLOSED
            reason = "WEEKEND"
        elif local.date() in self.holidays:
            status = UsEquitySessionStatus.CLOSED
            reason = "CONFIGURED_MARKET_HOLIDAY"
        elif time(4, 0) <= local_clock < time(9, 30):
            status = UsEquitySessionStatus.PRE_MARKET
            reason = "PRE_MARKET_04_00_09_30_ET"
        elif time(9, 30) <= local_clock < time(16, 0):
            status = UsEquitySessionStatus.REGULAR
            reason = "REGULAR_SESSION_09_30_16_00_ET"
        elif time(16, 0) <= local_clock < time(20, 0):
            status = UsEquitySessionStatus.AFTER_HOURS
            reason = "AFTER_HOURS_16_00_20_00_ET"
        else:
            status = UsEquitySessionStatus.CLOSED
            reason = "OUTSIDE_EXTENDED_HOURS"

        calendar = (
            "weekday_clock_with_configured_holidays"
            if self.holidays
            else "weekday_clock_no_holidays"
        )
        return UsEquitySession(
            status=status,
            as_of=normalized_as_of,
            local_time=local,
            calendar=calendar,
            reason=reason,
        )


class Hip3InstrumentRegistry:
    """Discovers an allowlisted HIP-3 universe through read-only info requests.

    It does not import or call the exchange SDK and is not wired into the LIVE
    executor. Missing markets remain in the result with explicit statuses so a UI
    or PAPER engine never has to interpret an absent row.
    """

    def __init__(
        self,
        client: ReadOnlyInfoClient,
        *,
        dex_name: str = "xyz",
        symbols: Iterable[str] = XYZ_US_EQUITY_SYMBOLS,
        session_clock: UsEquitySessionClock | None = None,
    ) -> None:
        normalized_dex = dex_name.strip().lower()
        if not normalized_dex:
            raise ValueError("dex_name cannot be empty")
        normalized_symbols = tuple(
            dict.fromkeys(symbol.strip().upper() for symbol in symbols if symbol.strip())
        )
        if not normalized_symbols:
            raise ValueError("at least one symbol is required")
        self.client = client
        self.dex_name = normalized_dex
        self.symbols = normalized_symbols
        self.session_clock = session_clock or UsEquitySessionClock()

    def discover(self, *, as_of: datetime | None = None) -> InstrumentRegistrySnapshot:
        observed_at = as_of or datetime.now(timezone.utc)
        session = self.session_clock.at(observed_at)

        try:
            dex_catalog = self.client.post({"type": "perpDexs"})
        except Exception as exc:  # the snapshot must explain upstream outages
            return self._unavailable_snapshot(
                session,
                DexDiscoveryStatus.DATA_UNAVAILABLE,
                f"PERP_DEX_DISCOVERY_FAILED:{type(exc).__name__}",
                warning="HIP3_DEX_CATALOG_UNAVAILABLE",
            )

        if not isinstance(dex_catalog, list):
            return self._unavailable_snapshot(
                session,
                DexDiscoveryStatus.DATA_UNAVAILABLE,
                "INVALID_PERP_DEX_CATALOG",
                warning="HIP3_DEX_CATALOG_INVALID",
            )

        dex_metadata = next(
            (
                item
                for item in dex_catalog
                if isinstance(item, dict)
                and str(item.get("name") or "").strip().lower() == self.dex_name
            ),
            None,
        )
        if dex_metadata is None:
            return self._unavailable_snapshot(
                session,
                DexDiscoveryStatus.NOT_DISCOVERED,
                "DEX_NOT_PRESENT_IN_PERP_DEX_CATALOG",
                warning="HIP3_DEX_NOT_DISCOVERED",
            )

        try:
            raw_meta_and_contexts = self.client.post(
                {"type": "metaAndAssetCtxs", "dex": self.dex_name}
            )
            meta, contexts = _parse_meta_and_contexts(raw_meta_and_contexts)
        except Exception as exc:
            return self._unavailable_snapshot(
                session,
                DexDiscoveryStatus.DATA_UNAVAILABLE,
                f"DEX_METADATA_FAILED:{type(exc).__name__}",
                dex_metadata=dex_metadata,
                warning="HIP3_DEX_METADATA_UNAVAILABLE",
            )

        total_net_deposit: float | None = None
        status_reason = "DEX_AND_METADATA_DISCOVERED"
        warnings: list[str] = []
        try:
            raw_status = self.client.post(
                {"type": "perpDexStatus", "dex": self.dex_name}
            )
            if isinstance(raw_status, dict):
                total_net_deposit = _finite_float(raw_status.get("totalNetDeposit"))
            else:
                warnings.append("HIP3_DEX_STATUS_INVALID")
                status_reason = "DEX_DISCOVERED_STATUS_INVALID"
        except Exception:
            # Status telemetry is optional; valid metadata still makes instruments
            # discoverable and prevents a transient endpoint failure from hiding them.
            warnings.append("HIP3_DEX_STATUS_UNAVAILABLE")
            status_reason = "DEX_DISCOVERED_STATUS_UNAVAILABLE"

        venue = VenueDescriptor(
            name=self.dex_name,
            full_name=_optional_string(dex_metadata.get("fullName")),
            deployer=_optional_string(dex_metadata.get("deployer")),
            status=DexDiscoveryStatus.DISCOVERED,
            status_reason=status_reason,
            total_net_deposit=total_net_deposit,
        )

        discovered: dict[str, tuple[int, dict[str, Any], dict[str, Any] | None]] = {}
        duplicate_symbols: set[str] = set()
        universe = meta.get("universe")
        assert isinstance(universe, list)  # guaranteed by _parse_meta_and_contexts
        for index, raw_instrument in enumerate(universe):
            if not isinstance(raw_instrument, dict):
                continue
            normalized_symbol = _underlying_symbol(
                raw_instrument.get("name"), self.dex_name
            )
            if normalized_symbol not in self.symbols:
                continue
            if normalized_symbol in discovered:
                duplicate_symbols.add(normalized_symbol)
                continue
            context = contexts[index] if index < len(contexts) else None
            discovered[normalized_symbol] = (
                index,
                raw_instrument,
                context if isinstance(context, dict) else None,
            )

        if duplicate_symbols:
            warnings.append("HIP3_DUPLICATE_INSTRUMENTS_IGNORED")

        instruments = tuple(
            self._instrument_from_discovery(
                symbol,
                discovered.get(symbol),
                session,
            )
            for symbol in self.symbols
        )
        return InstrumentRegistrySnapshot(
            as_of=session.as_of,
            venue=venue,
            session=session,
            instruments=instruments,
            warnings=tuple(warnings),
        )

    def _instrument_from_discovery(
        self,
        symbol: str,
        discovered: tuple[int, dict[str, Any], dict[str, Any] | None] | None,
        session: UsEquitySession,
    ) -> Instrument:
        venue_symbol = f"{self.dex_name}:{symbol}"
        common = {
            "instrument_id": f"hyperliquid:{self.dex_name}:{symbol}",
            "symbol": symbol,
            "venue_symbol": venue_symbol,
            "venue": self.dex_name,
            "session_status": session.status,
            "session_timezone": session.timezone,
        }
        if discovered is None:
            return Instrument(
                **common,
                venue_status=VenueMarketStatus.NOT_LISTED,
                venue_status_reason="TARGET_NOT_PRESENT_IN_DEX_UNIVERSE",
            )

        universe_index, metadata, context = discovered
        raw_venue_symbol = _optional_string(metadata.get("name")) or venue_symbol
        status, status_reason = _market_status(metadata, context)
        return Instrument(
            **{**common, "venue_symbol": raw_venue_symbol},
            venue_status=status,
            venue_status_reason=status_reason,
            mark_px=_positive_float(context.get("markPx")) if context else None,
            mid_px=_positive_float(context.get("midPx")) if context else None,
            oracle_px=_positive_float(context.get("oraclePx")) if context else None,
            previous_day_px=(
                _positive_float(context.get("prevDayPx")) if context else None
            ),
            day_notional_volume_usd=(
                _non_negative_float(context.get("dayNtlVlm")) if context else None
            ),
            open_interest=(
                _non_negative_float(context.get("openInterest")) if context else None
            ),
            funding_rate=_finite_float(context.get("funding")) if context else None,
            size_decimals=_non_negative_int(metadata.get("szDecimals")),
            max_leverage=_positive_int(metadata.get("maxLeverage")),
            only_isolated=_optional_bool(metadata.get("onlyIsolated")),
            universe_index=universe_index,
        )

    def _unavailable_snapshot(
        self,
        session: UsEquitySession,
        status: DexDiscoveryStatus,
        status_reason: str,
        *,
        warning: str,
        dex_metadata: dict[str, Any] | None = None,
    ) -> InstrumentRegistrySnapshot:
        metadata = dex_metadata or {}
        venue = VenueDescriptor(
            name=self.dex_name,
            status=status,
            status_reason=status_reason,
            full_name=_optional_string(metadata.get("fullName")),
            deployer=_optional_string(metadata.get("deployer")),
        )
        venue_market_status = (
            VenueMarketStatus.NOT_LISTED
            if status is DexDiscoveryStatus.NOT_DISCOVERED
            else VenueMarketStatus.DATA_UNAVAILABLE
        )
        instruments = tuple(
            Instrument(
                instrument_id=f"hyperliquid:{self.dex_name}:{symbol}",
                symbol=symbol,
                venue_symbol=f"{self.dex_name}:{symbol}",
                venue=self.dex_name,
                venue_status=venue_market_status,
                venue_status_reason=status_reason,
                session_status=session.status,
                session_timezone=session.timezone,
            )
            for symbol in self.symbols
        )
        return InstrumentRegistrySnapshot(
            as_of=session.as_of,
            venue=venue,
            session=session,
            instruments=instruments,
            warnings=(warning,),
        )


def _parse_meta_and_contexts(payload: Any) -> tuple[dict[str, Any], list[Any]]:
    if not isinstance(payload, list) or len(payload) != 2:
        raise ValueError("invalid metaAndAssetCtxs response")
    meta, contexts = payload
    if not isinstance(meta, dict) or not isinstance(meta.get("universe"), list):
        raise ValueError("invalid HIP-3 metadata")
    if not isinstance(contexts, list):
        raise ValueError("invalid HIP-3 asset contexts")
    return meta, contexts


def _underlying_symbol(raw_name: Any, dex_name: str) -> str | None:
    if not isinstance(raw_name, str):
        return None
    name = raw_name.strip()
    if not name:
        return None
    if ":" not in name:
        return name.upper()
    prefix, symbol = name.split(":", 1)
    if prefix.lower() != dex_name:
        return None
    return symbol.upper()


def _market_status(
    metadata: dict[str, Any], context: dict[str, Any] | None
) -> tuple[VenueMarketStatus, str]:
    raw_status = str(metadata.get("status") or "").strip().lower()
    if metadata.get("isDelisted") is True or raw_status == "delisted":
        return VenueMarketStatus.DELISTED, "DEX_METADATA_MARKS_INSTRUMENT_DELISTED"
    if metadata.get("isHalted") is True or raw_status in {
        "halted",
        "paused",
        "settled",
    }:
        return VenueMarketStatus.HALTED, "DEX_METADATA_MARKS_INSTRUMENT_HALTED"
    if context is None:
        return VenueMarketStatus.DATA_UNAVAILABLE, "ASSET_CONTEXT_UNAVAILABLE"
    if _positive_float(context.get("markPx")) is None:
        return VenueMarketStatus.DATA_UNAVAILABLE, "MARK_PRICE_UNAVAILABLE"
    return VenueMarketStatus.AVAILABLE, "DEX_METADATA_AND_MARK_AVAILABLE"


def _optional_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _finite_float(value: Any) -> float | None:
    try:
        converted = float(value)
    except (TypeError, ValueError):
        return None
    return converted if math.isfinite(converted) else None


def _positive_float(value: Any) -> float | None:
    converted = _finite_float(value)
    return converted if converted is not None and converted > 0 else None


def _non_negative_float(value: Any) -> float | None:
    converted = _finite_float(value)
    return converted if converted is not None and converted >= 0 else None


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        converted = int(value)
    except (TypeError, ValueError):
        return None
    return converted if converted > 0 else None


def _non_negative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        converted = int(value)
    except (TypeError, ValueError):
        return None
    return converted if converted >= 0 else None


def _optional_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None
