from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentMode(str, Enum):
    DRY_RUN = "dry_run"
    PAPER = "paper"
    TESTNET = "testnet"
    SUPERVISED = "supervised"
    LIVE = "live"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    agent_mode: AgentMode = AgentMode.PAPER
    database_url: str = "sqlite:///./agent.db"
    paper_equity_usd: float = Field(default=10_000.0, gt=0)
    trading_profile: Literal["conservative", "experimental"] = "conservative"
    max_model_leverage: int = Field(default=5, ge=1, le=50)
    automation_enabled: bool = False
    cycle_interval_seconds: float = Field(default=300.0, ge=60, le=3600)
    risk_monitor_interval_seconds: float = Field(default=10.0, ge=5, le=60)
    x_research_cache_seconds: float = Field(default=900.0, ge=60, le=3600)
    strategist_refresh_seconds: float = Field(default=1800.0, ge=300, le=14_400)
    min_llm_collateral_usd: float = Field(default=10.0, ge=0)
    trader_max_interval_seconds: float = Field(default=900.0, ge=300, le=3600)
    trader_move_trigger_pct: float = Field(default=0.5, gt=0, le=10)
    capital_constrained_review_seconds: float = Field(default=1800.0, ge=300, le=14_400)
    position_alert_distance_pct: float = Field(default=1.0, gt=0, le=10)

    market_data_provider: Literal["paper", "hyperliquid"] = "paper"
    hyperliquid_network: Literal["mainnet", "testnet"] = "mainnet"
    hyperliquid_account_address: str | None = None
    hyperliquid_private_key: SecretStr | None = None
    hyperliquid_execution_network: Literal["mainnet", "testnet"] = "testnet"
    hyperliquid_margin_mode: Literal["isolated", "cross"] = "isolated"
    hyperliquid_timeout_seconds: float = Field(default=10.0, gt=1, le=30)
    testnet_confirmation: str | None = None
    testnet_max_order_notional_usd: float = Field(default=25.0, gt=0, le=100)
    testnet_slippage_bps: int = Field(default=50, ge=1, le=100)
    mainnet_slippage_bps: int = Field(default=30, ge=1, le=100)

    experimental_min_plan_conviction: float = Field(default=0.25, ge=0, le=1)
    experimental_min_open_confidence: float = Field(default=0.30, ge=0, le=1)
    experimental_max_portfolio_risk_frac: float = Field(default=0.08, gt=0, le=0.25)
    experimental_max_net_exposure_frac: float = Field(default=2.0, gt=0, le=3)
    experimental_max_asset_notional_usd: float = Field(default=7_500.0, gt=0)

    llm_provider: str = "rules"
    xai_api_key: str | None = None
    xai_model: str = "grok-4.20-0309-reasoning"
    x_search_enabled: bool = False
    x_allowed_handles: str = ""

    live_confirmation: str | None = None
    live_automation_confirmation: str | None = None
    guardrails_configured: bool = True
    market_data_max_age_seconds: float = Field(default=30.0, gt=0)
    max_daily_loss_frac: float = Field(default=0.03, gt=0, le=0.25)
    max_drawdown_frac: float = Field(default=0.08, gt=0, le=0.5)

    cors_origins: str = "http://localhost:5173,http://localhost:4173"

    @model_validator(mode="after")
    def _safety_interlocks(self) -> "Settings":
        provider = self.llm_provider.strip().lower()
        if provider not in {"rules", "grok"}:
            raise ValueError("LLM_PROVIDER must be 'rules' or 'grok'")
        if provider == "grok" and not self.xai_api_key:
            raise ValueError("XAI_API_KEY is required when LLM_PROVIDER=grok")
        if self.x_search_enabled and not self.xai_api_key:
            raise ValueError("XAI_API_KEY is required when X_SEARCH_ENABLED=true")
        if self.hyperliquid_account_address:
            address = self.hyperliquid_account_address
            if not (
                len(address) == 42
                and address.startswith("0x")
                and all(c in "0123456789abcdefABCDEF" for c in address[2:])
            ):
                raise ValueError("HYPERLIQUID_ACCOUNT_ADDRESS must be a 42-char hex address")

        if self.agent_mode is AgentMode.TESTNET:
            if self.testnet_confirmation != "I_UNDERSTAND_TESTNET":
                raise ValueError("TESTNET_CONFIRMATION is missing or invalid")
            if not self.hyperliquid_account_address or not self.hyperliquid_private_key:
                raise ValueError(
                    "HYPERLIQUID_ACCOUNT_ADDRESS and HYPERLIQUID_PRIVATE_KEY are required "
                    "for TESTNET"
                )
            if not self.guardrails_configured:
                raise ValueError("A non-empty guardrail configuration is required")
            if not self.database_url.startswith("postgresql"):
                raise ValueError("TESTNET requires a durable PostgreSQL DATABASE_URL")
            if self.hyperliquid_execution_network != "testnet":
                raise ValueError("TESTNET requires HYPERLIQUID_EXECUTION_NETWORK=testnet")

        if self.agent_mode is AgentMode.LIVE:
            if self.live_confirmation != "I_UNDERSTAND_THE_RISKS":
                raise ValueError("LIVE_CONFIRMATION is missing or invalid")
            if self.automation_enabled and (
                self.live_automation_confirmation
                != "I_UNDERSTAND_LIVE_AUTOMATION"
            ):
                raise ValueError("LIVE_AUTOMATION_CONFIRMATION is missing or invalid")
            if not self.guardrails_configured:
                raise ValueError("A non-empty guardrail configuration is required")
            if not self.hyperliquid_account_address or not self.hyperliquid_private_key:
                raise ValueError(
                    "HYPERLIQUID_ACCOUNT_ADDRESS and HYPERLIQUID_PRIVATE_KEY are required "
                    "for LIVE"
                )
            if self.hyperliquid_execution_network != "mainnet":
                raise ValueError("LIVE requires HYPERLIQUID_EXECUTION_NETWORK=mainnet")
            if not self.database_url.startswith("postgresql"):
                raise ValueError("LIVE requires a durable PostgreSQL DATABASE_URL")

        if self.agent_mode is AgentMode.SUPERVISED:
            raise ValueError(
                "SUPERVISED is intentionally unavailable; use PAPER, TESTNET, or LIVE"
            )
        return self

    @property
    def allowed_x_handles(self) -> list[str]:
        handles = [h.strip().lstrip("@") for h in self.x_allowed_handles.split(",")]
        return [h for h in handles if h][:20]

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def hyperliquid_api_url(self) -> str:
        if self.hyperliquid_network == "testnet":
            return "https://api.hyperliquid-testnet.xyz"
        return "https://api.hyperliquid.xyz"

    @property
    def hyperliquid_execution_api_url(self) -> str:
        if self.hyperliquid_execution_network == "testnet":
            return "https://api.hyperliquid-testnet.xyz"
        return "https://api.hyperliquid.xyz"
