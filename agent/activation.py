from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from enum import Enum
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ActivationMode(str, Enum):
    ALWAYS = "always"
    US_EQUITIES = "us_equities"
    CRYPTO_SESSIONS = "crypto_sessions"
    HYBRID = "hybrid"


class UsEquitySession(str, Enum):
    PREMARKET = "premarket"
    MARKET_OPEN = "market_open"
    FIRST_HOURS = "first_hours"
    BEFORE_CLOSE = "before_close"
    AFTER_HOURS = "after_hours"


class CryptoSession(str, Enum):
    ASIA = "asia"
    EUROPE = "europe"
    US = "us"
    EUROPE_US_OVERLAP = "europe_us_overlap"


def validate_timezone_name(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("activation timezone cannot be empty")
    try:
        ZoneInfo(normalized)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(
            f"unknown IANA activation timezone: {normalized}"
        ) from exc
    return normalized


class ActivationConfig(BaseModel):
    """Runtime activation policy, independent from trading decisions.

    Market hours are evaluated in their canonical venue timezone. ``timezone``
    only controls how boundaries are rendered to the operator, so choosing a
    display timezone can never move the actual US or crypto session.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: ActivationMode = ActivationMode.ALWAYS
    timezone: str = "UTC"
    us_equities_sessions: list[UsEquitySession] = Field(
        default_factory=lambda: [
            UsEquitySession.MARKET_OPEN,
            UsEquitySession.FIRST_HOURS,
            UsEquitySession.BEFORE_CLOSE,
        ]
    )
    crypto_sessions: list[CryptoSession] = Field(
        default_factory=lambda: [
            CryptoSession.ASIA,
            CryptoSession.EUROPE,
            CryptoSession.US,
        ]
    )
    liquidity_filter_enabled: bool = False
    liquidity_min_24h_volume_usd: float = Field(default=25_000_000.0, ge=0)
    liquidity_min_open_interest_usd: float = Field(default=10_000_000.0, ge=0)
    liquidity_min_eligible_assets: int = Field(default=1, ge=1, le=8)

    @field_validator("timezone")
    @classmethod
    def _valid_timezone(cls, value: str) -> str:
        return validate_timezone_name(value)

    @field_validator("us_equities_sessions", "crypto_sessions")
    @classmethod
    def _unique_sessions(cls, value: list[Any]) -> list[Any]:
        if len(value) != len(set(value)):
            raise ValueError("activation sessions cannot contain duplicates")
        return value

    @model_validator(mode="after")
    def _coherent_mode(self) -> "ActivationConfig":
        if self.mode is ActivationMode.US_EQUITIES and not self.us_equities_sessions:
            raise ValueError("us_equities mode requires at least one US session")
        if self.mode is ActivationMode.CRYPTO_SESSIONS and not self.crypto_sessions:
            raise ValueError("crypto_sessions mode requires at least one crypto session")
        if self.mode is ActivationMode.HYBRID:
            if not self.us_equities_sessions and not self.crypto_sessions:
                raise ValueError("hybrid mode requires at least one selected session")
            if not self.liquidity_filter_enabled:
                raise ValueError("hybrid mode requires the liquidity filter")
        return self

    def public_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "timezone": self.timezone,
            "us_equities_sessions": [item.value for item in self.us_equities_sessions],
            "crypto_sessions": [item.value for item in self.crypto_sessions],
            "liquidity_filter": {
                "enabled": self.liquidity_filter_enabled,
                "min_24h_volume_usd": self.liquidity_min_24h_volume_usd,
                "min_open_interest_usd": self.liquidity_min_open_interest_usd,
                "min_eligible_assets": self.liquidity_min_eligible_assets,
            },
        }


class LiquidityAssetObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    symbol: str = Field(min_length=1, max_length=32)
    volume_24h_usd: float | None = Field(default=None, ge=0)
    open_interest_usd: float | None = Field(default=None, ge=0)


class LiquidityObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    as_of: datetime
    assets: list[LiquidityAssetObservation] = Field(default_factory=list, max_length=64)
    source: str = Field(default="market_data", min_length=1, max_length=80)


class ActivationDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state: Literal["ACTIVE", "WAITING", "BLOCKED"]
    reason: str
    detail: str
    evaluated_at: datetime
    timezone: str
    active_sessions: list[str] = Field(default_factory=list)
    active_window_ends_at: datetime | None = None
    active_window_ends_local: datetime | None = None
    next_window_at: datetime | None = None
    next_window_local: datetime | None = None
    schedule_basis: str
    liquidity: dict[str, Any]


@dataclass(frozen=True)
class _Window:
    name: str
    start: datetime
    end: datetime


_US_WINDOWS: dict[UsEquitySession, tuple[time, time]] = {
    UsEquitySession.PREMARKET: (time(4, 0), time(9, 30)),
    UsEquitySession.MARKET_OPEN: (time(9, 30), time(10, 0)),
    UsEquitySession.FIRST_HOURS: (time(9, 30), time(12, 0)),
    UsEquitySession.BEFORE_CLOSE: (time(15, 0), time(16, 0)),
    UsEquitySession.AFTER_HOURS: (time(16, 0), time(20, 0)),
}

_CRYPTO_WINDOWS: dict[CryptoSession, tuple[time, time]] = {
    CryptoSession.ASIA: (time(0, 0), time(8, 0)),
    CryptoSession.EUROPE: (time(7, 0), time(16, 0)),
    CryptoSession.US: (time(13, 0), time(22, 0)),
    CryptoSession.EUROPE_US_OVERLAP: (time(13, 0), time(16, 0)),
}


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("activation evaluation requires a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _dates_around(at: datetime, zone: ZoneInfo) -> list[date]:
    local_date = at.astimezone(zone).date()
    return [local_date + timedelta(days=offset) for offset in range(-1, 9)]


def _us_windows(config: ActivationConfig, at: datetime) -> list[_Window]:
    market_zone = ZoneInfo("America/New_York")
    windows: list[_Window] = []
    for local_date in _dates_around(at, market_zone):
        if local_date.weekday() >= 5:
            continue
        for session in config.us_equities_sessions:
            start_at, end_at = _US_WINDOWS[session]
            start = datetime.combine(local_date, start_at, market_zone)
            end = datetime.combine(local_date, end_at, market_zone)
            windows.append(_Window(f"us_equities:{session.value}", start, end))
    return windows


def _crypto_windows(config: ActivationConfig, at: datetime) -> list[_Window]:
    market_zone = ZoneInfo("UTC")
    windows: list[_Window] = []
    for local_date in _dates_around(at, market_zone):
        for session in config.crypto_sessions:
            start_at, end_at = _CRYPTO_WINDOWS[session]
            start = datetime.combine(local_date, start_at, market_zone)
            end = datetime.combine(local_date, end_at, market_zone)
            windows.append(_Window(f"crypto:{session.value}", start, end))
    return windows


def _configured_windows(config: ActivationConfig, at: datetime) -> list[_Window]:
    if config.mode is ActivationMode.US_EQUITIES:
        return _us_windows(config, at)
    if config.mode is ActivationMode.CRYPTO_SESSIONS:
        return _crypto_windows(config, at)
    if config.mode is ActivationMode.HYBRID:
        return _us_windows(config, at) + _crypto_windows(config, at)
    return []


def evaluate_session_window(
    config: ActivationConfig,
    *,
    at: datetime,
) -> ActivationDecision:
    """Evaluate only the clock gate; this function never touches market data."""

    evaluated_at = _utc(at)
    display_zone = ZoneInfo(config.timezone)
    filter_summary = {
        "enabled": config.liquidity_filter_enabled,
        "status": "NOT_EVALUATED",
        "eligible_assets": [],
        "eligible_count": 0,
        "observed_count": 0,
        "minimum_eligible_assets": config.liquidity_min_eligible_assets,
    }
    if config.mode is ActivationMode.ALWAYS:
        return ActivationDecision(
            state="ACTIVE",
            reason="ALWAYS_ACTIVE",
            detail="Permanent activation mode is enabled (24/7).",
            evaluated_at=evaluated_at,
            timezone=config.timezone,
            schedule_basis="permanent_24_7",
            liquidity=filter_summary,
        )

    windows = _configured_windows(config, evaluated_at)
    active = [
        item
        for item in windows
        if item.start.astimezone(timezone.utc)
        <= evaluated_at
        < item.end.astimezone(timezone.utc)
    ]
    future = [
        item
        for item in windows
        if item.start.astimezone(timezone.utc) > evaluated_at
    ]
    next_window = min(future, key=lambda item: item.start) if future else None
    active_end = max((item.end for item in active), default=None)
    basis = (
        "America/New_York weekday schedule; exchange holidays are not inferred"
        if config.mode is ActivationMode.US_EQUITIES
        else "UTC crypto sessions"
        if config.mode is ActivationMode.CRYPTO_SESSIONS
        else "union of America/New_York weekday and UTC crypto sessions"
    )
    common: dict[str, Any] = {
        "evaluated_at": evaluated_at,
        "timezone": config.timezone,
        "active_sessions": [item.name for item in active],
        "active_window_ends_at": (
            active_end.astimezone(timezone.utc) if active_end else None
        ),
        "active_window_ends_local": (
            active_end.astimezone(display_zone) if active_end else None
        ),
        "next_window_at": (
            next_window.start.astimezone(timezone.utc) if next_window else None
        ),
        "next_window_local": (
            next_window.start.astimezone(display_zone) if next_window else None
        ),
        "schedule_basis": basis,
        "liquidity": filter_summary,
    }
    if active:
        names = ", ".join(item.name for item in active)
        return ActivationDecision(
            state="ACTIVE",
            reason="ACTIVATION_WINDOW_OPEN",
            detail=f"Selected activation window is open: {names}.",
            **common,
        )
    return ActivationDecision(
        state="WAITING",
        reason="OUTSIDE_ACTIVATION_WINDOW",
        detail=(
            "No selected activation session is currently open; the next exact "
            "window is reported in the configured display timezone."
        ),
        **common,
    )


def evaluate_activation(
    config: ActivationConfig,
    *,
    at: datetime,
    observation: LiquidityObservation | dict[str, Any] | None = None,
) -> ActivationDecision:
    """Pure, deterministic clock and liquidity evaluation.

    The bootstrap ``LIQUIDITY_OBSERVATION_PENDING`` state tells the caller to
    make one deterministic market-data probe. It never calls an LLM and cannot
    accidentally authorize a cycle without the requested evidence.
    """

    session = evaluate_session_window(config, at=at)
    if session.state != "ACTIVE" or not config.liquidity_filter_enabled:
        liquidity = dict(session.liquidity)
        liquidity["status"] = (
            "DISABLED" if not config.liquidity_filter_enabled else "DEFERRED"
        )
        return session.model_copy(update={"liquidity": liquidity})

    if observation is None:
        liquidity = dict(session.liquidity)
        liquidity["status"] = "PENDING_OBSERVATION"
        return session.model_copy(
            update={
                "state": "BLOCKED",
                "reason": "LIQUIDITY_OBSERVATION_PENDING",
                "detail": (
                    "The activation window is open; one deterministic market-data "
                    "probe is required before any LLM cycle can start."
                ),
                "liquidity": liquidity,
            }
        )

    observation = LiquidityObservation.model_validate(observation)
    needs_volume = config.liquidity_min_24h_volume_usd > 0
    needs_oi = config.liquidity_min_open_interest_usd > 0
    evaluable: list[LiquidityAssetObservation] = []
    eligible: list[LiquidityAssetObservation] = []
    for asset in observation.assets:
        if needs_volume and asset.volume_24h_usd is None:
            continue
        if needs_oi and asset.open_interest_usd is None:
            continue
        evaluable.append(asset)
        if (
            (not needs_volume or asset.volume_24h_usd >= config.liquidity_min_24h_volume_usd)
            and (
                not needs_oi
                or asset.open_interest_usd >= config.liquidity_min_open_interest_usd
            )
        ):
            eligible.append(asset)

    liquidity = {
        "enabled": True,
        "status": "PASSED",
        "source": observation.source,
        "as_of": observation.as_of.isoformat(),
        "eligible_assets": [item.symbol for item in eligible],
        "eligible_count": len(eligible),
        "observed_count": len(observation.assets),
        "evaluable_count": len(evaluable),
        "minimum_eligible_assets": config.liquidity_min_eligible_assets,
        "min_24h_volume_usd": config.liquidity_min_24h_volume_usd,
        "min_open_interest_usd": config.liquidity_min_open_interest_usd,
    }
    if len(evaluable) < config.liquidity_min_eligible_assets:
        liquidity["status"] = "DATA_UNAVAILABLE"
        return session.model_copy(
            update={
                "state": "BLOCKED",
                "reason": "LIQUIDITY_DATA_UNAVAILABLE",
                "detail": (
                    "The filter is enabled but too few assets contain both required "
                    "24h-volume and open-interest observations."
                ),
                "liquidity": liquidity,
            }
        )
    if len(eligible) < config.liquidity_min_eligible_assets:
        liquidity["status"] = "BELOW_THRESHOLDS"
        return session.model_copy(
            update={
                "state": "WAITING",
                "reason": "LIQUIDITY_FILTER_NOT_MET",
                "detail": (
                    f"{len(eligible)} eligible asset(s); "
                    f"{config.liquidity_min_eligible_assets} required by the "
                    "deterministic volume/liquidity filter."
                ),
                "liquidity": liquidity,
            }
        )
    return session.model_copy(
        update={
            "reason": "ACTIVATION_CONDITIONS_MET",
            "detail": (
                f"Activation window is open and {len(eligible)} asset(s) meet the "
                "deterministic volume/liquidity thresholds."
            ),
            "liquidity": liquidity,
        }
    )
