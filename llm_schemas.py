"""
Couche décision LLM — schémas de sortie (stratège + trader) et feature sheet.

Frontière I2 : une sortie LLM n'existe pour le système que si elle parse ici
(validation structurelle Pydantic, extra="forbid"), puis passe llm_checks.py
(validation contextuelle déterministe), puis le moteur de guardrails.
Tout le reste est un incident loggé.

Principe : le LLM décide du QUOI (biais, thèse, niveaux, timing, allocation
relative). Le code décide du COMBIEN (taille, levier, caps). Aucun champ de
ces schémas n'exprime une taille absolue en USD ou en contrats.
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agent.symbols import ALL_SYMBOLS

Symbol = Annotated[str, Field(pattern=r"^(BTC|ETH|SOL|XRP|BNB|HYPE|LINK|SUI|xyz:(TSLA|NVDA|AAPL|MSFT|AMZN|META|GOOGL))$")]
WHITELIST: tuple[str, ...] = ALL_SYMBOLS

# Sorties LLM : immuables, aucun champ inconnu toléré.
STRICT = ConfigDict(extra="forbid", frozen=True)


# ---------------------------------------------------------------------------
# Sortie du STRATÈGE : le playbook
# ---------------------------------------------------------------------------

class AssetPlan(BaseModel):
    """Plan par actif. Le trader ne peut OPEN que dans le sens de `bias`."""
    model_config = STRICT

    symbol: Symbol
    bias: Literal["LONG", "SHORT", "FLAT"]
    conviction: float = Field(ge=0.0, le=1.0)
    thesis: str = Field(min_length=20, max_length=800)
    entry_zone: Optional[tuple[float, float]] = Field(
        default=None,
        description="(low, high). None = entrée au marché autorisée si toutes "
                    "les autres conditions passent.",
    )
    invalidation_px: Optional[float] = Field(default=None, gt=0)
    targets: list[float] = Field(default_factory=list, max_length=4)
    risk_alloc: float = Field(
        ge=0.0, le=1.0,
        description="Fraction du budget de risque global. La valeur en USD de "
                    "ce budget est inconnue du LLM, par design.",
    )

    @field_validator("invalidation_px", mode="before")
    @classmethod
    def _zero_invalidation_means_absent(cls, value: object) -> object:
        """Tolerate the common structured-output encoding of null as numeric zero.

        Directional plans remain protected: zero becomes None and the coherence
        validator below still rejects LONG/SHORT without a real invalidation.
        """
        if value in (0, 0.0, "0", "0.0"):
            return None
        return value

    @model_validator(mode="after")
    def _coherence(self) -> "AssetPlan":
        if self.bias == "FLAT":
            if (self.entry_zone is not None
                    or self.invalidation_px is not None
                    or self.targets
                    or self.risk_alloc != 0.0):
                raise ValueError(
                    f"{self.symbol}: un plan FLAT ne porte ni entry_zone, ni "
                    f"invalidation_px, ni targets, et risk_alloc doit être 0"
                )
            return self

        # bias LONG / SHORT
        if self.invalidation_px is None:
            raise ValueError(
                f"{self.symbol}: invalidation_px obligatoire quand bias={self.bias}"
            )
        if self.risk_alloc <= 0.0:
            raise ValueError(
                f"{self.symbol}: risk_alloc doit être > 0 quand bias={self.bias}"
            )
        if any(t <= 0 for t in self.targets):
            raise ValueError(f"{self.symbol}: les targets doivent être > 0")

        if self.entry_zone is not None:
            lo, hi = self.entry_zone
            if not (0.0 < lo <= hi):
                raise ValueError(
                    f"{self.symbol}: entry_zone doit être (low, high) avec "
                    f"0 < low <= high"
                )
            if self.bias == "LONG" and self.invalidation_px >= lo:
                raise ValueError(
                    f"{self.symbol}: LONG → invalidation_px sous la zone d'entrée"
                )
            if self.bias == "SHORT" and self.invalidation_px <= hi:
                raise ValueError(
                    f"{self.symbol}: SHORT → invalidation_px au-dessus de la "
                    f"zone d'entrée"
                )
            for t in self.targets:
                if self.bias == "LONG" and t <= hi:
                    raise ValueError(
                        f"{self.symbol}: LONG → targets au-dessus de la zone"
                    )
                if self.bias == "SHORT" and t >= lo:
                    raise ValueError(
                        f"{self.symbol}: SHORT → targets sous la zone"
                    )

        if self.targets:
            expected = sorted(self.targets, reverse=(self.bias == "SHORT"))
            if list(self.targets) != expected:
                raise ValueError(
                    f"{self.symbol}: targets ordonnés du plus proche au plus "
                    f"lointain dans le sens du trade"
                )
            if len(set(self.targets)) != len(self.targets):
                raise ValueError(f"{self.symbol}: targets dupliqués")
        return self


class PlaybookLLMOutput(BaseModel):
    """Ce que le stratège émet. L'identité, le temps et la provenance sont
    ajoutés côté code (PlaybookRecord) — jamais par le LLM."""
    model_config = STRICT

    regime_view: str = Field(min_length=20, max_length=400)
    plans: list[AssetPlan] = Field(min_length=3, max_length=8)
    changes_vs_previous: str = Field(min_length=10, max_length=600)
    ttl_hours: int = Field(ge=4, le=12)

    @model_validator(mode="after")
    def _portfolio(self) -> "PlaybookLLMOutput":
        symbols = sorted(p.symbol for p in self.plans)
        if len(symbols) != len(set(symbols)):
            raise ValueError(
                f"le playbook doit couvrir exactement {sorted(WHITELIST)}, "
                f"reçu {symbols}"
            )
        total = sum(p.risk_alloc for p in self.plans)
        if total > 1.0 + 1e-9:
            raise ValueError(f"somme des risk_alloc = {total:.3f} > 1.0")
        return self

    def plan_for(self, symbol: str) -> AssetPlan:
        return next(p for p in self.plans if p.symbol == symbol)


class PlaybookRecord(BaseModel):
    """Enregistrement persisté (Postgres). Construit par le code au moment de
    la persistance : expires_at = created_at + ttl_hours."""
    model_config = ConfigDict(extra="forbid")

    playbook_id: str
    version: int = Field(ge=1)
    created_at: datetime
    expires_at: datetime
    feature_sheet_hash: str          # sha256 → replay & métrique de déterminisme
    model_id: str
    prompt_version: str
    payload: PlaybookLLMOutput

    def is_expired(self, now: datetime) -> bool:
        return now >= self.expires_at


# ---------------------------------------------------------------------------
# Sortie du TRADER : les décisions du cycle
# ---------------------------------------------------------------------------

class AssetDecision(BaseModel):
    model_config = STRICT

    symbol: Symbol
    action: Literal["OPEN", "REDUCE", "CLOSE", "HOLD"]
    direction: Optional[Literal["LONG", "SHORT"]] = Field(
        default=None, description="Requis pour OPEN, interdit sinon."
    )
    size_frac: float = Field(
        default=0.0, ge=0.0, le=1.0,
        description="OPEN : fraction du risque planifié à déployer. "
                    "REDUCE : fraction de la position à couper. "
                    "CLOSE / HOLD : 0.",
    )
    leverage: int = Field(
        default=1,
        ge=1,
        le=50,
        description=(
            "Leverage requested for OPEN. It does not replace risk-based sizing and "
            "is ignored for HOLD/REDUCE/CLOSE. Deterministic code may cap it."
        ),
    )
    notional_usd: Optional[float] = Field(
        default=None,
        gt=0,
        description="Notionnel absolu proposé par le modèle pour OPEN. Ignoré sinon.",
    )
    horizon_hours: float = Field(
        default=8.0, ge=0.25, le=168.0,
        description="Horizon prévu, utilisé pour estimer le funding.",
    )
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=10, max_length=300)

    @model_validator(mode="after")
    def _coherence(self) -> "AssetDecision":
        if self.action == "OPEN":
            if self.direction is None:
                raise ValueError(f"{self.symbol}: OPEN exige direction")
            if self.size_frac <= 0.0:
                raise ValueError(f"{self.symbol}: OPEN exige size_frac > 0")
        elif self.action == "REDUCE":
            if self.direction is not None:
                raise ValueError(
                    f"{self.symbol}: REDUCE ne porte pas de direction "
                    f"(c'est celle de la position existante)"
                )
            if self.size_frac <= 0.0:
                raise ValueError(
                    f"{self.symbol}: REDUCE exige size_frac > 0 "
                    f"(fraction de la position à couper)"
                )
        else:  # CLOSE, HOLD
            if self.direction is not None:
                raise ValueError(
                    f"{self.symbol}: {self.action} ne porte pas de direction"
                )
            if self.size_frac != 0.0:
                raise ValueError(
                    f"{self.symbol}: {self.action} → size_frac = 0 "
                    f"(CLOSE = sortie totale implicite ; REDUCE pour le partiel)"
                )
        return self


class ConsequenceScenario(BaseModel):
    model_config = STRICT

    size_multiplier: float
    notional_usd: float
    stop_loss_usd: float
    stop_loss_equity_pct: float
    margin_used_usd: float
    funding_estimate_usd: float
    fees_estimate_usd: float
    slippage_estimate_usd: float


class DecisionConsequences(BaseModel):
    model_config = STRICT

    symbol: Symbol
    action: str
    assumptions: dict[str, float | str]
    proposed_notional_usd: float = 0.0
    stop_loss_usd: float = 0.0
    stop_loss_equity_pct: float = 0.0
    margin_used_usd: float = 0.0
    liquidation_px_estimate: Optional[float] = None
    liquidation_to_stop_atr: Optional[float] = None
    funding_estimate_usd: float = 0.0
    fees_estimate_usd: float = 0.0
    slippage_estimate_usd: float = 0.0
    gross_exposure_after_usd: float = 0.0
    net_exposure_after_usd: float = 0.0
    adverse_move_1atr_usd: float = 0.0
    adverse_move_2atr_usd: float = 0.0
    adverse_move_3atr_usd: float = 0.0
    scenarios: list[ConsequenceScenario] = Field(default_factory=list)
    operational_facts: dict[str, bool | str | float] = Field(default_factory=dict)


class ConsequenceReport(BaseModel):
    model_config = STRICT

    as_of: datetime
    disclaimer: str
    decisions: list[DecisionConsequences]


class DecisionRiskReview(BaseModel):
    model_config = STRICT

    symbol: Symbol
    decision: Literal["KEEP_AS_IS", "ADJUST", "CANCEL"]
    material_new_information: list[str] = Field(default_factory=list, max_length=6)
    reason: str = Field(min_length=10, max_length=500)
    adjusted_decision: Optional[AssetDecision] = None

    @model_validator(mode="after")
    def _adjustment(self) -> "DecisionRiskReview":
        if self.decision == "ADJUST" and self.adjusted_decision is None:
            raise ValueError("ADJUST exige adjusted_decision")
        if self.decision != "ADJUST" and self.adjusted_decision is not None:
            raise ValueError("adjusted_decision est réservé à ADJUST")
        if self.adjusted_decision and self.adjusted_decision.symbol != self.symbol:
            raise ValueError("symbol mismatch dans adjusted_decision")
        return self


class FinalRiskReview(BaseModel):
    model_config = STRICT

    reviews: list[DecisionRiskReview] = Field(min_length=3, max_length=8)

    @model_validator(mode="after")
    def _coverage(self) -> "FinalRiskReview":
        if len({item.symbol for item in self.reviews}) != len(self.reviews):
            raise ValueError("risk review doit couvrir exactement l'univers analysé")
        return self


class TraderOutput(BaseModel):
    model_config = STRICT

    decisions: list[AssetDecision] = Field(min_length=3, max_length=8)
    request_strategist_review: bool = False
    review_reason: Optional[str] = Field(
        default=None, min_length=10, max_length=300
    )

    @model_validator(mode="after")
    def _coverage(self) -> "TraderOutput":
        symbols = sorted(d.symbol for d in self.decisions)
        if len(symbols) != len(set(symbols)):
            raise ValueError(
                f"exactement une décision par actif ({sorted(WHITELIST)}), "
                f"reçu {symbols}"
            )
        if self.request_strategist_review and not self.review_reason:
            raise ValueError(
                "review_reason obligatoire quand request_strategist_review=true"
            )
        if self.review_reason is not None and not self.request_strategist_review:
            raise ValueError(
                "review_reason présent mais request_strategist_review=false"
            )
        return self

    def decision_for(self, symbol: str) -> AssetDecision:
        return next(d for d in self.decisions if d.symbol == symbol)


# ---------------------------------------------------------------------------
# Entrée des deux LLM : le feature sheet (construit par le code, jamais par
# le LLM ; hashé en sha256 → PlaybookRecord.feature_sheet_hash)
# ---------------------------------------------------------------------------

class AdvisorSignal(BaseModel):
    """Avis d'une stratégie naïve du repo public. Indicatif : le stratège est
    libre de l'ignorer."""
    model_config = STRICT

    strategy_id: str
    direction: Literal["LONG", "SHORT", "FLAT"]
    conviction: float = Field(ge=0.0, le=1.0)
    features: dict[str, float] = Field(default_factory=dict)


class AssetFeatures(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: Symbol
    mark_px: float = Field(gt=0)
    max_leverage: int = Field(default=20, ge=1, le=100)

    # Tendance / volatilité multi-UT
    ret_1h_pct: float
    ret_4h_pct: float
    ret_1d_pct: float
    ret_7d_pct: float
    atr_4h: float = Field(gt=0)
    adx_4h: float = Field(ge=0)
    donchian_pos_4h: float = Field(ge=0.0, le=1.0)   # 0 = bas du canal 20p, 1 = haut
    dist_ema20_4h_atr: float                          # (mark - ema20_4h) / atr_4h
    dist_ema200_1d_atr: float
    rv_24h_ann_pct: float = Field(ge=0)
    rv_7d_ann_pct: float = Field(ge=0)

    # Dérivés Hyperliquid
    funding_1h_pct: float
    funding_24h_avg_pct: float
    oi_usd: float = Field(ge=0)
    oi_change_24h_pct: float
    liq_longs_24h_usd: float = Field(ge=0)
    liq_shorts_24h_usd: float = Field(ge=0)

    # Niveaux
    swing_high_4h: float = Field(gt=0)
    swing_low_4h: float = Field(gt=0)
    vwap_1d: float = Field(gt=0)

    # Microstructure / fraîcheur
    spread_bps: float = Field(ge=0)
    data_age_seconds: float = Field(ge=0)

    advisors: list[AdvisorSignal] = Field(default_factory=list)


class FeatureSheet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    as_of: datetime
    assets: list[AssetFeatures] = Field(min_length=3, max_length=8)
    corr_30d_btc_eth: float = Field(ge=-1.0, le=1.0)
    corr_30d_btc_sol: float = Field(ge=-1.0, le=1.0)

    @model_validator(mode="after")
    def _coverage(self) -> "FeatureSheet":
        symbols = sorted(a.symbol for a in self.assets)
        if len(symbols) != len(set(symbols)):
            raise ValueError(
                f"le feature sheet doit couvrir exactement {sorted(WHITELIST)}"
            )
        return self
