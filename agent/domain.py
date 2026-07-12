from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from llm_schemas import ConsequenceReport, FinalRiskReview, PlaybookRecord, TraderOutput

from agent.activation import (
    ActivationMode,
    CryptoSession,
    UsEquitySession,
    validate_timezone_name,
)

Symbol = Annotated[str, Field(pattern=r"^(BTC|ETH|SOL|XRP|BNB|HYPE|LINK|SUI|xyz:(TSLA|NVDA|AAPL|MSFT|AMZN|META|GOOGL))$")]


class KillSwitchState(str, Enum):
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    HALTED = "HALTED"


class CycleStatus(str, Enum):
    RUNNING = "RUNNING"
    SKIPPED = "SKIPPED"
    FAILED = "FAILED"
    DEGRADED = "DEGRADED"
    COMPLETED = "COMPLETED"


class StrategySignal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy_id: str
    symbol: Symbol
    direction: Literal["LONG", "SHORT", "FLAT"]
    score: float = Field(ge=-1.0, le=1.0)
    conviction: float = Field(ge=0.0, le=1.0)
    rationale: str


class ResearchSignal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    symbol: Symbol
    direction: Literal["LONG", "SHORT", "FLAT"] = "FLAT"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    novelty: float = Field(default=0.0, ge=0.0, le=1.0)
    manipulation_risk: float = Field(default=0.0, ge=0.0, le=1.0)
    horizon_minutes: int = Field(default=60, ge=5, le=1440)
    summary: str = "No verified event signal."
    source_urls: list[str] = Field(
        default_factory=list,
        max_length=8,
        validation_alias=AliasChoices("source_urls", "sources"),
    )

    @property
    def sources(self) -> list[str]:
        """Read-only compatibility for code handling pre-v2 cached payloads."""
        return self.source_urls


class ResearchBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    as_of: datetime
    signals: list[ResearchSignal] = Field(min_length=3, max_length=8)

    @model_validator(mode="after")
    def _coverage(self) -> "ResearchBundle":
        if len({s.symbol for s in self.signals}) != len(self.signals):
            raise ValueError("research contains duplicate symbols")
        return self


class PromptPosition(BaseModel):
    """Live position state passed to the trading model."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    symbol: Symbol
    side: Literal["LONG", "SHORT"]
    entry_px: float = Field(gt=0)
    mark_px: float = Field(gt=0)
    order_type: Literal["MARKET", "LIMIT"] = "MARKET"
    limit_px: float | None = None
    invalidation_px: float = Field(gt=0)
    notional_usd: float = Field(gt=0)
    leverage: int = Field(ge=1)
    unrealized_pnl_usd: float
    roe_pct: float
    unrealized_r: float
    distance_to_invalidation_atr: float = Field(ge=0)
    exit_management: str = "FIXED"
    place_stop_order: bool = True
    take_profit_fractions: list[float] = Field(default_factory=list, max_length=4)
    trailing_stop_pct: float | None = None
    time_stop_hours: float | None = None
    move_to_break_even_at_r: float | None = None


class StructuredReason(BaseModel):
    """Stable, UI-friendly explanation backed by machine-readable evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str = Field(min_length=3, max_length=80, pattern=r"^[A-Z0-9_]+$")
    message: str = Field(min_length=5, max_length=300)
    impact: Literal["SUPPORTS", "REDUCES", "BLOCKS", "NEUTRAL"]
    evidence: dict[str, Any] = Field(default_factory=dict)


class ConvictionDiagnostic(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    symbol: Symbol
    conviction: float = Field(ge=0.0, le=1.0)
    level: Literal["LOW", "MODERATE", "HIGH"]
    actionable: bool
    reasons: list[StructuredReason] = Field(default_factory=list, max_length=8)


class DecisionBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    playbook: PlaybookRecord
    trader: TraderOutput
    provider: str
    provenance: Literal["GROK", "CACHE", "RULE_FALLBACK", "SAFE_HOLD"] = (
        "RULE_FALLBACK"
    )
    status: Literal["NOMINAL", "DEGRADED"] = "NOMINAL"
    reasons: list[StructuredReason] = Field(default_factory=list, max_length=8)
    conviction_diagnostics: list[ConvictionDiagnostic] = Field(
        default_factory=list, max_length=8
    )
    initial_trader: TraderOutput | None = None
    consequence_report: ConsequenceReport | None = None
    risk_review: FinalRiskReview | None = None


class GuardrailVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    symbol: Symbol
    action: str
    verdict: Literal["APPROVE", "MODIFY", "REJECT", "SKIP"]
    reasons: list[str] = Field(default_factory=list)
    notional_usd: float = Field(default=0.0, ge=0)
    leverage: int = Field(default=1, ge=1, le=50)


class ApprovedOrder(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cycle_id: str
    playbook_id: str
    symbol: Symbol
    action: Literal["OPEN", "REDUCE", "CLOSE"]
    direction: Literal["LONG", "SHORT"]
    notional_usd: float = Field(gt=0)
    mark_px: float = Field(gt=0)
    order_type: Literal["MARKET", "LIMIT"] = "MARKET"
    limit_px: float | None = None
    invalidation_px: float = Field(gt=0)
    targets: list[float] = Field(default_factory=list, max_length=4)
    place_stop_order: bool = False
    take_profit_fractions: list[float] = Field(default_factory=list, max_length=4)
    exit_management: str = "DYNAMIC"
    trailing_stop_pct: float | None = None
    time_stop_hours: float | None = None
    move_to_break_even_at_r: float | None = None
    leverage: int = Field(default=1, ge=1, le=50)
    decision_key: str


class ExecutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    intent_id: str
    cloid: str
    symbol: Symbol
    status: str
    duplicate_prevented: bool = False


class KillSwitchCommand(BaseModel):
    state: KillSwitchState
    reason: str = Field(min_length=5, max_length=300)
    actor: str = Field(default="dashboard", min_length=2, max_length=80)


class AutomationCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    cycle_interval_seconds: float | None = Field(default=None, ge=60, le=3600)
    risk_monitor_interval_seconds: float | None = Field(default=None, ge=5, le=60)
    activation_mode: ActivationMode | None = None
    activation_timezone: str | None = None
    us_equities_sessions: list[UsEquitySession] | None = None
    crypto_sessions: list[CryptoSession] | None = None
    liquidity_filter_enabled: bool | None = None
    liquidity_min_24h_volume_usd: float | None = Field(default=None, ge=0)
    liquidity_min_open_interest_usd: float | None = Field(default=None, ge=0)
    liquidity_min_eligible_assets: int | None = Field(default=None, ge=1, le=8)

    @field_validator("activation_timezone")
    @classmethod
    def _valid_activation_timezone(cls, value: str | None) -> str | None:
        return validate_timezone_name(value) if value is not None else None

    @field_validator("us_equities_sessions", "crypto_sessions")
    @classmethod
    def _unique_sessions(cls, value: list[Any] | None) -> list[Any] | None:
        if value is not None and len(value) != len(set(value)):
            raise ValueError("activation sessions cannot contain duplicates")
        return value
