"""
Validation contextuelle déterministe + sizing de la couche LLM.

Fonctions pures (aucun I/O) → testables unitairement, injectables telles
quelles dans le moteur de guardrails.

Sémantique :
- check_* retourne une liste de Violation. Liste vide = pas d'objection.
  Toute violation sur un OPEN = REJECT du guardrail.
- size_open_order retourne un SizedOrder. was_capped=True avec notional > 0
  = verdict MODIFY. notional_usd == 0 (MIN_NOTIONAL) = REJECT.

Asymétrie volontaire : les actions risk-reducing (REDUCE / CLOSE) ne sont
soumises qu'au check d'existence de position — pas de check de fraîcheur ni
de playbook. Réduire le risque sur données vieilles vaut toujours mieux que
garder le risque.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from llm_schemas import AssetDecision, AssetPlan, PlaybookRecord


class Violation(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    message: str


class LLMLayerConfig(BaseModel):
    """Sous-ensemble de la config guardrails propre à la couche LLM.
    Source unique de vérité : ces valeurs sont aussi injectées dans les
    placeholders {{...}} des prompts pour que prompts et code ne divergent
    jamais."""
    model_config = ConfigDict(extra="forbid")

    min_stop_atr: float = 0.5
    max_stop_atr: float = 3.0
    entry_zone_tolerance_pct: float = 0.0015
    min_plan_conviction: float = 0.5
    min_open_confidence: float = 0.6
    max_opens_per_day_per_symbol: int = 3
    stop_out_cooldown_minutes: float = 120.0
    max_portfolio_risk_frac: float = 0.02
    max_net_exposure_frac: float = 0.6
    max_leverage: int = Field(default=2, ge=1, le=50)
    operational_only: bool = False
    min_order_notional_usd: float = 25.0
    market_data_max_age_seconds: float = 30.0   # I4
    playbook_ttl_min_hours: int = 4
    playbook_ttl_max_hours: int = 12
    max_asset_notional_usd: dict[str, float] = Field(
        default_factory=lambda: {symbol: 2_000.0 for symbol in ("BTC", "ETH", "SOL", "XRP", "BNB", "HYPE", "LINK", "SUI")}
    )


class AssetSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str
    mark_px: float = Field(gt=0)
    atr_4h: float = Field(gt=0)
    spread_bps: float = Field(ge=0)
    data_age_seconds: float = Field(ge=0)
    max_leverage: int = Field(default=20, ge=1, le=100)


class PositionState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str
    side: Literal["LONG", "SHORT"]
    notional_usd: float = Field(gt=0)
    entry_px: float = Field(gt=0)
    invalidation_px: float = Field(gt=0)


class PortfolioContext(BaseModel):
    """Construit côté code. equity_usd n'apparaît jamais dans un prompt."""
    model_config = ConfigDict(extra="forbid")

    equity_usd: float = Field(gt=0)
    positions: list[PositionState] = Field(default_factory=list)
    opens_today: dict[str, int] = Field(default_factory=dict)
    minutes_since_stop_out: dict[str, float] = Field(default_factory=dict)

    def position_for(self, symbol: str) -> Optional[PositionState]:
        return next((p for p in self.positions if p.symbol == symbol), None)

    def net_exposure_usd(self) -> float:
        return sum(
            p.notional_usd if p.side == "LONG" else -p.notional_usd
            for p in self.positions
        )


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def _invalidation_violations(
    plan: AssetPlan, snap: AssetSnapshot, cfg: LLMLayerConfig
) -> list[Violation]:
    """Invalidation du bon côté du prix courant, à distance raisonnable en ATR."""
    v: list[Violation] = []
    inv = plan.invalidation_px
    assert inv is not None  # garanti par le schéma quand bias != FLAT

    if plan.bias == "LONG" and inv >= snap.mark_px:
        return [Violation(
            code="INVALIDATION_WRONG_SIDE",
            message=f"{plan.symbol}: LONG mais invalidation {inv} >= mark {snap.mark_px}",
        )]
    if plan.bias == "SHORT" and inv <= snap.mark_px:
        return [Violation(
            code="INVALIDATION_WRONG_SIDE",
            message=f"{plan.symbol}: SHORT mais invalidation {inv} <= mark {snap.mark_px}",
        )]

    dist_atr = abs(snap.mark_px - inv) / snap.atr_4h
    if dist_atr < cfg.min_stop_atr:
        v.append(Violation(
            code="INVALIDATION_TOO_TIGHT",
            message=f"{plan.symbol}: stop à {dist_atr:.2f} ATR < {cfg.min_stop_atr}",
        ))
    elif dist_atr > cfg.max_stop_atr:
        v.append(Violation(
            code="INVALIDATION_TOO_WIDE",
            message=f"{plan.symbol}: stop à {dist_atr:.2f} ATR > {cfg.max_stop_atr}",
        ))
    return v


def check_plan_against_market(
    plan: AssetPlan, snap: AssetSnapshot, cfg: LLMLayerConfig
) -> list[Violation]:
    """À exécuter à la validation du playbook (post-parsing) ET au moment de
    chaque OPEN (le marché a pu bouger depuis l'écriture du plan)."""
    if plan.bias == "FLAT":
        return []
    v = _invalidation_violations(plan, snap, cfg)
    if plan.targets:
        if plan.bias == "LONG" and min(plan.targets) <= snap.mark_px:
            v.append(Violation(
                code="TARGETS_BEHIND_PRICE",
                message=f"{plan.symbol}: target(s) déjà sous le prix courant",
            ))
        if plan.bias == "SHORT" and max(plan.targets) >= snap.mark_px:
            v.append(Violation(
                code="TARGETS_BEHIND_PRICE",
                message=f"{plan.symbol}: target(s) déjà au-dessus du prix courant",
            ))
    return v


def check_decision(
    decision: AssetDecision,
    plan: AssetPlan,
    playbook: PlaybookRecord,
    snap: AssetSnapshot,
    pf: PortfolioContext,
    cfg: LLMLayerConfig,
    now: datetime,
) -> list[Violation]:
    """Toutes les objections contextuelles à une décision du trader."""
    if decision.action == "HOLD":
        return []

    pos = pf.position_for(decision.symbol)

    # Risk-reducing : volontairement permissif.
    if decision.action in ("REDUCE", "CLOSE"):
        if pos is None:
            return [Violation(
                code="NO_POSITION",
                message=f"{decision.symbol}: {decision.action} sans position ouverte",
            )]
        return []

    if cfg.operational_only:
        v: list[Violation] = []
        if pos is not None:
            v.append(Violation(code="ALREADY_IN_POSITION", message=f"{decision.symbol}: position déjà ouverte"))
        if snap.data_age_seconds > cfg.market_data_max_age_seconds:
            v.append(Violation(code="DATA_STALE", message=f"{decision.symbol}: données périmées"))
        if decision.notional_usd is None:
            v.append(Violation(code="NOTIONAL_NOT_EXPLICIT", message=f"{decision.symbol}: notionnel USD absent"))
        elif decision.notional_usd / max(decision.leverage, 1) > pf.equity_usd:
            v.append(Violation(code="INSUFFICIENT_COLLATERAL", message=f"{decision.symbol}: marge supérieure au collatéral disponible"))
        if decision.leverage > min(cfg.max_leverage, snap.max_leverage):
            v.append(Violation(code="VENUE_LEVERAGE_CAP", message=f"{decision.symbol}: levier supérieur à la limite"))
        if plan.invalidation_px is None:
            v.append(Violation(code="STOP_MISSING", message=f"{decision.symbol}: stop absent"))
        else:
            liquidation = snap.mark_px * (1 - 1 / decision.leverage) if decision.direction == "LONG" else snap.mark_px * (1 + 1 / decision.leverage)
            before_stop = liquidation >= plan.invalidation_px if decision.direction == "LONG" else liquidation <= plan.invalidation_px
            if before_stop:
                v.append(Violation(code="LIQUIDATION_BEFORE_STOP", message=f"{decision.symbol}: liquidation estimée avant stop"))
        return v

    # OPEN : la totale.
    v: list[Violation] = []

    if playbook.is_expired(now):
        v.append(Violation(
            code="PLAYBOOK_EXPIRED",
            message=f"playbook v{playbook.version} expiré ({playbook.expires_at.isoformat()})",
        ))
    if plan.bias == "FLAT":
        v.append(Violation(
            code="BIAS_FLAT",
            message=f"{decision.symbol}: OPEN sur un plan FLAT",
        ))
    elif decision.direction != plan.bias:
        v.append(Violation(
            code="BIAS_MISMATCH",
            message=f"{decision.symbol}: OPEN {decision.direction} vs bias {plan.bias}",
        ))
    if pos is not None:
        v.append(Violation(
            code="ALREADY_IN_POSITION",
            message=f"{decision.symbol}: position déjà ouverte (one position per symbol)",
        ))
    if snap.data_age_seconds > cfg.market_data_max_age_seconds:
        v.append(Violation(
            code="DATA_STALE",
            message=f"{decision.symbol}: données de {snap.data_age_seconds:.0f}s "
                    f"> {cfg.market_data_max_age_seconds:.0f}s (I4)",
        ))
    if plan.conviction < cfg.min_plan_conviction:
        v.append(Violation(
            code="LOW_PLAN_CONVICTION",
            message=f"{decision.symbol}: conviction du plan {plan.conviction:.2f} "
                    f"< {cfg.min_plan_conviction}",
        ))
    if decision.confidence < cfg.min_open_confidence:
        v.append(Violation(
            code="LOW_CONFIDENCE",
            message=f"{decision.symbol}: confidence {decision.confidence:.2f} "
                    f"< {cfg.min_open_confidence}",
        ))
    if plan.entry_zone is not None:
        lo, hi = plan.entry_zone
        tol = cfg.entry_zone_tolerance_pct
        if not (lo * (1 - tol) <= snap.mark_px <= hi * (1 + tol)):
            v.append(Violation(
                code="OUT_OF_ENTRY_ZONE",
                message=f"{decision.symbol}: mark {snap.mark_px} hors zone "
                        f"[{lo}, {hi}] (tol {tol:.2%})",
            ))
    if plan.bias != "FLAT":
        v += _invalidation_violations(plan, snap, cfg)
    if pf.opens_today.get(decision.symbol, 0) >= cfg.max_opens_per_day_per_symbol:
        v.append(Violation(
            code="OVERTRADE_LIMIT",
            message=f"{decision.symbol}: {cfg.max_opens_per_day_per_symbol} "
                    f"OPEN déjà atteints aujourd'hui",
        ))
    m = pf.minutes_since_stop_out.get(decision.symbol)
    if m is not None and m < cfg.stop_out_cooldown_minutes:
        v.append(Violation(
            code="STOP_OUT_COOLDOWN",
            message=f"{decision.symbol}: stop-out il y a {m:.0f} min "
                    f"< cooldown {cfg.stop_out_cooldown_minutes:.0f} min",
        ))
    return v


# ---------------------------------------------------------------------------
# Sizing — 100 % déterministe. Le LLM ne voit jamais ces nombres.
# ---------------------------------------------------------------------------

class SizedOrder(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    symbol: str
    direction: Literal["LONG", "SHORT"]
    notional_usd: float = Field(ge=0)
    risk_usd: float = Field(ge=0)
    was_capped: bool
    cap_reasons: list[str]


def size_open_order(
    decision: AssetDecision,
    plan: AssetPlan,
    snap: AssetSnapshot,
    pf: PortfolioContext,
    cfg: LLMLayerConfig,
) -> SizedOrder:
    """Sizing R-based :
        risk_usd  = equity × max_portfolio_risk_frac × risk_alloc × size_frac
        qty       = risk_usd / |mark - invalidation|
        notional  = qty × mark
    puis caps dans l'ordre : notionnel par actif → exposition nette
    (corrélation BTC/ETH/SOL) → plancher min_order_notional_usd.
    """
    assert decision.action == "OPEN" and decision.direction is not None
    assert plan.invalidation_px is not None

    stop_dist = abs(snap.mark_px - plan.invalidation_px)
    if decision.notional_usd is not None:
        notional = decision.notional_usd
        risk_usd = (notional / snap.mark_px) * stop_dist
    else:
        # ``size_frac`` is a margin allocation on total account equity. Leverage
        # converts that margin allocation into exposure; it is never based only
        # on the remaining free cash.
        notional = pf.equity_usd * decision.size_frac * decision.leverage
        risk_usd = (notional / snap.mark_px) * stop_dist
    reasons: list[str] = []

    cap_asset = cfg.max_asset_notional_usd.get(decision.symbol, 0.0)
    if notional > cap_asset:
        notional = cap_asset
        reasons.append("ASSET_NOTIONAL_CAP")

    sign = 1.0 if decision.direction == "LONG" else -1.0
    cap_net = cfg.max_net_exposure_frac * pf.equity_usd
    headroom = max(0.0, cap_net - sign * pf.net_exposure_usd())
    if notional > headroom:
        notional = headroom
        reasons.append("NET_EXPOSURE_CAP")

    if notional < cfg.min_order_notional_usd:
        notional = 0.0
        reasons.append("MIN_NOTIONAL")

    return SizedOrder(
        symbol=decision.symbol,
        direction=decision.direction,
        notional_usd=notional,
        risk_usd=risk_usd,
        was_capped=bool(reasons),
        cap_reasons=reasons,
    )
