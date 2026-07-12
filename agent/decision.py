from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Protocol

from llm_checks import LLMLayerConfig
from llm_schemas import (
    AssetDecision,
    AssetPlan,
    DecisionRiskReview,
    FeatureSheet,
    FinalRiskReview,
    PlaybookLLMOutput,
    PlaybookRecord,
    TraderOutput,
)

from agent.domain import (
    ConvictionDiagnostic,
    DecisionBundle,
    PromptPosition,
    ResearchBundle,
    ResearchSignal,
    StrategySignal,
    StructuredReason,
)
from agent.consequence import simulate_consequences
from agent.llm_observability import llm_failure_record, llm_record


class DecisionProvider(Protocol):
    name: str

    def decide(
        self,
        feature_sheet: FeatureSheet,
        signals: list[StrategySignal],
        research: ResearchBundle,
        positions: list[dict],
        equity_usd: float | None = None,
        cycle_id: str | None = None,
        allow_strategist_refresh: bool = True,
    ) -> DecisionBundle: ...


def _feature_hash(feature_sheet: FeatureSheet) -> str:
    payload = json.dumps(feature_sheet.model_dump(mode="json"), sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def build_conviction_diagnostics(
    playbook: PlaybookRecord,
    signals: list[StrategySignal],
    research: ResearchBundle,
    min_plan_conviction: float,
) -> list[ConvictionDiagnostic]:
    """Explain conviction without changing any decision or guardrail threshold."""
    research_by_symbol = {item.symbol: item for item in research.signals}
    diagnostics: list[ConvictionDiagnostic] = []
    for plan in playbook.payload.plans:
        reasons: list[StructuredReason] = []
        if plan.bias == "FLAT":
            reasons.append(StructuredReason(
                code="PLAN_IS_FLAT",
                message="The strategist found no directional plan for this asset.",
                impact="BLOCKS",
                evidence={"bias": plan.bias},
            ))
        if plan.conviction < min_plan_conviction:
            reasons.append(StructuredReason(
                code="BELOW_PLAN_CONVICTION_THRESHOLD",
                message="Plan conviction is below the configured action threshold.",
                impact="BLOCKS",
                evidence={
                    "conviction": plan.conviction,
                    "threshold": min_plan_conviction,
                },
            ))

        technical = [item for item in signals if item.symbol == plan.symbol]
        directional = [item for item in technical if item.direction != "FLAT"]
        directions = sorted({item.direction for item in directional})
        if not directional:
            reasons.append(StructuredReason(
                code="NO_DIRECTIONAL_TECHNICAL_EDGE",
                message="Technical advisors provide no directional confirmation.",
                impact="REDUCES",
                evidence={
                    "advisor_count": len(technical),
                    "scores": [round(item.score, 4) for item in technical],
                },
            ))
        elif len(directions) > 1:
            reasons.append(StructuredReason(
                code="CONFLICTING_TECHNICAL_SIGNALS",
                message="Technical advisors disagree on direction.",
                impact="REDUCES",
                evidence={"directions": directions},
            ))
        elif plan.bias != "FLAT" and directions[0] == plan.bias:
            reasons.append(StructuredReason(
                code="TECHNICAL_ALIGNMENT",
                message="Directional technical signals support the plan bias.",
                impact="SUPPORTS",
                evidence={"direction": directions[0]},
            ))
        elif plan.bias != "FLAT":
            reasons.append(StructuredReason(
                code="TECHNICAL_PLAN_MISMATCH",
                message="Technical direction does not support the plan bias.",
                impact="REDUCES",
                evidence={"technical_direction": directions[0], "plan_bias": plan.bias},
            ))

        event = research_by_symbol.get(plan.symbol)
        if event is None or not event.source_urls:
            reasons.append(StructuredReason(
                code="NO_VERIFIED_CATALYST",
                message="No cited external source confirms a market catalyst.",
                impact="REDUCES",
                evidence={
                    "research_confidence": event.confidence if event else 0.0,
                    "source_count": len(event.source_urls) if event else 0,
                },
            ))
        elif event.direction == "FLAT" or event.confidence == 0:
            reasons.append(StructuredReason(
                code="RESEARCH_SIGNAL_NEUTRAL",
                message="Verified research does not provide a directional event edge.",
                impact="REDUCES",
                evidence={
                    "direction": event.direction,
                    "confidence": event.confidence,
                    "source_count": len(event.source_urls),
                },
            ))
        elif plan.bias != "FLAT" and event.direction != plan.bias:
            reasons.append(StructuredReason(
                code="RESEARCH_PLAN_CONFLICT",
                message="External research points against the plan bias.",
                impact="REDUCES",
                evidence={
                    "research_direction": event.direction,
                    "plan_bias": plan.bias,
                    "confidence": event.confidence,
                },
            ))
        elif plan.bias != "FLAT":
            reasons.append(StructuredReason(
                code="RESEARCH_ALIGNMENT",
                message="External research supports the plan bias.",
                impact="SUPPORTS",
                evidence={
                    "direction": event.direction,
                    "confidence": event.confidence,
                    "source_count": len(event.source_urls),
                },
            ))
        if event is not None and event.manipulation_risk > event.confidence:
            reasons.append(StructuredReason(
                code="MANIPULATION_RISK_DOMINATES",
                message="Manipulation risk exceeds research confidence.",
                impact="REDUCES",
                evidence={
                    "manipulation_risk": event.manipulation_risk,
                    "research_confidence": event.confidence,
                },
            ))

        level = (
            "LOW" if plan.conviction < 0.5
            else "HIGH" if plan.conviction >= 0.8
            else "MODERATE"
        )
        diagnostics.append(ConvictionDiagnostic(
            symbol=plan.symbol,
            conviction=plan.conviction,
            level=level,
            actionable=(
                plan.bias != "FLAT" and plan.conviction >= min_plan_conviction
            ),
            reasons=reasons[:8],
        ))
    return diagnostics


class RuleBasedDecisionProvider:
    """Deterministic PAPER baseline used to measure any future LLM uplift."""

    name = "rules-v1"

    def decide(
        self,
        feature_sheet: FeatureSheet,
        signals: list[StrategySignal],
        research: ResearchBundle,
        positions: list[dict],
        equity_usd: float | None = None,
        cycle_id: str | None = None,
        allow_strategist_refresh: bool = True,
    ) -> DecisionBundle:
        del equity_usd, cycle_id, allow_strategist_refresh
        positions_by_symbol = {p["symbol"]: p for p in positions}
        research_by_symbol = {r.symbol: r for r in research.signals}
        plans: list[AssetPlan] = []
        raw: dict[str, float] = {}

        for asset in feature_sheet.assets:
            technical = [s.score for s in signals if s.symbol == asset.symbol]
            technical_score = sum(technical) / max(1, len(technical))
            event = research_by_symbol.get(asset.symbol, ResearchSignal(symbol=asset.symbol))
            event_sign = {"LONG": 1.0, "SHORT": -1.0, "FLAT": 0.0}[event.direction]
            event_score = (
                event_sign
                * event.confidence
                * event.novelty
                * (1.0 - event.manipulation_risk)
            )
            raw[asset.symbol] = 0.85 * technical_score + 0.15 * event_score

        active = [symbol for symbol, score in raw.items() if abs(score) >= 0.30]
        allocation = min(0.5, 0.9 / max(1, len(active)))

        for asset in feature_sheet.assets:
            score = raw[asset.symbol]
            if abs(score) < 0.30:
                plans.append(
                    AssetPlan(
                        symbol=asset.symbol,
                        bias="FLAT",
                        conviction=min(0.49, abs(score)),
                        thesis=(
                            f"No aligned edge: aggregate score {score:.2f}, ADX "
                            f"{asset.adx_4h:.1f}, 4h return {asset.ret_4h_pct:.2f}%."
                        ),
                        risk_alloc=0.0,
                    )
                )
                continue

            bias = "LONG" if score > 0 else "SHORT"
            mark, atr = asset.mark_px, asset.atr_4h
            zone = (mark * 0.997, mark * 1.003)
            if bias == "LONG":
                invalidation = mark - 1.5 * atr
                targets = [mark + 2.0 * atr, mark + 3.0 * atr]
            else:
                invalidation = mark + 1.5 * atr
                targets = [mark - 2.0 * atr, mark - 3.0 * atr]
            plans.append(
                AssetPlan(
                    symbol=asset.symbol,
                    bias=bias,
                    conviction=min(0.95, 0.58 + abs(score) * 0.6),
                    thesis=(
                        f"Aggregate score {score:.2f} with ADX {asset.adx_4h:.1f}, "
                        f"4h return {asset.ret_4h_pct:.2f}% and funding "
                        f"{asset.funding_1h_pct:.3f}%."
                    ),
                    entry_zone=zone,
                    invalidation_px=invalidation,
                    targets=targets,
                    risk_alloc=allocation,
                )
            )

        playbook_output = PlaybookLLMOutput(
            regime_view=(
                "Rule baseline: trend-following only when technical strategies align; "
                "social research is capped at fifteen percent of the aggregate score."
            ),
            plans=plans,
            changes_vs_previous="Fresh PAPER-cycle playbook generated from current features.",
            ttl_hours=8,
        )
        now = datetime.now(timezone.utc)
        playbook = PlaybookRecord(
            playbook_id=f"pb-{_feature_hash(feature_sheet)[:16]}",
            version=1,
            created_at=now,
            expires_at=now + timedelta(hours=8),
            feature_sheet_hash=_feature_hash(feature_sheet),
            model_id=self.name,
            prompt_version="rules-v1",
            payload=playbook_output,
        )

        decisions: list[AssetDecision] = []
        for plan in plans:
            if plan.bias == "FLAT" or plan.symbol in positions_by_symbol:
                decisions.append(
                    AssetDecision(
                        symbol=plan.symbol,
                        action="HOLD",
                        confidence=plan.conviction,
                        rationale="No new risk: plan is FLAT or a position already exists.",
                    )
                )
            else:
                decisions.append(
                    AssetDecision(
                        symbol=plan.symbol,
                        action="OPEN",
                        direction=plan.bias,
                        size_frac=0.75,
                        confidence=max(0.62, plan.conviction),
                        rationale="Current mark is inside the validated entry zone.",
                    )
                )
        return DecisionBundle(
            playbook=playbook,
            trader=TraderOutput(decisions=decisions),
            provider=self.name,
            provenance="RULE_FALLBACK",
            status="NOMINAL",
            reasons=[StructuredReason(
                code="DETERMINISTIC_RULE_PROVIDER",
                message="The configured deterministic rule provider produced this decision.",
                impact="NEUTRAL",
                evidence={"provider": self.name},
            )],
            conviction_diagnostics=build_conviction_diagnostics(
                playbook,
                signals,
                research,
                LLMLayerConfig().min_plan_conviction,
            ),
        )


class GrokDecisionProvider:
    name = "grok"

    def __init__(
        self,
        api_key: str,
        model: str,
        config: LLMLayerConfig | None = None,
        trading_profile: str = "conservative",
        strategist_refresh_seconds: float = 1800.0,
        live_autonomous: bool = False,
        recorder: Callable[[dict], None] | None = None,
    ):
        from openai import OpenAI

        self.client = OpenAI(api_key=api_key, base_url="https://api.x.ai/v1")
        self.model = model
        self.config = config or LLMLayerConfig()
        self.trading_profile = trading_profile
        self.strategist_refresh_seconds = strategist_refresh_seconds
        self.live_autonomous = live_autonomous
        self._playbook_cache: PlaybookRecord | None = None
        self.recorder = recorder
        root = Path(__file__).resolve().parents[1]
        self.strategist_prompt = (root / "prompt_strategist.md").read_text(encoding="utf-8")
        self.trader_prompt = (root / "prompt_trader.md").read_text(encoding="utf-8")
        self.risk_review_prompt = (root / "prompt_risk_review.md").read_text(encoding="utf-8")

    def _strategist_system_prompt(self) -> str:
        if self.live_autonomous:
            directive = (
                "LIVE autonomous trading: rank all assets and build every setup you "
                "would genuinely trade. You control risk_alloc without an application "
                "notional cap. Use current price, volatility, funding, research and "
                "existing position state; never fabricate evidence. Every directional "
                "plan must include a valid stop and ordered profit targets."
            )
        elif self.trading_profile == "experimental":
            directive = (
                "PAPER/TESTNET experiment: rank the available assets and seek actionable "
                "relative edges. Unless data is stale/corrupt or every setup violates a "
                "hard structural rule, produce at least one non-FLAT plan. Mixed evidence "
                "should reduce conviction and risk_alloc rather than automatically force "
                "FLAT. Never fabricate supporting numbers."
            )
        else:
            directive = (
                "Conservative profile: prefer FLAT when evidence is mixed and prioritize "
                "low turnover over exploration."
            )
        return (
            self.strategist_prompt
            .replace("{{profile_directive}}", directive)
            .replace("{{min_plan_conviction}}", f"{self.config.min_plan_conviction:g}")
            .replace("{{ttl_min_hours}}", str(self.config.playbook_ttl_min_hours))
            .replace("{{ttl_max_hours}}", str(self.config.playbook_ttl_max_hours))
        )

    def seed_playbook(self, payload: dict | None) -> None:
        if payload is not None:
            candidate = PlaybookRecord.model_validate(payload)
            if len(candidate.payload.plans) >= 3:
                self._playbook_cache = candidate

    def _trader_system_prompt(self) -> str:
        if self.live_autonomous:
            directive = (
                "LIVE autonomous trading: independently choose OPEN, HOLD, REDUCE or "
                "CLOSE for every asset. You may open multiple eligible positions in one "
                "cycle. Choose size_frac and leverage freely up to each asset's "
                "max_leverage in SNAPSHOT. Treat HOLD as a deliberate decision, not a "
                "default. Existing stops, targets and unrealized R must influence "
                "position management."
            )
        elif self.trading_profile == "experimental":
            directive = (
                "PAPER/TESTNET experiment: HOLD is not the default. When flat and at least "
                "one valid non-FLAT playbook plan exists, OPEN the highest-confidence "
                "eligible setup; prefer one new position per cycle. Do not wait for "
                "textbook alignment, but never bypass a hard structural condition."
            )
        else:
            directive = (
                "Conservative profile: HOLD is the default and OPEN requires strong, "
                "multi-signal alignment."
            )
        return (
            self.trader_prompt
            .replace("{{profile_directive}}", directive)
            .replace("{{min_open_confidence}}", f"{self.config.min_open_confidence:g}")
            .replace("{{max_leverage}}", str(self.config.max_leverage))
        )

    def decide(
        self,
        feature_sheet: FeatureSheet,
        signals: list[StrategySignal],
        research: ResearchBundle,
        positions: list[dict],
        equity_usd: float | None = None,
        cycle_id: str | None = None,
        allow_strategist_refresh: bool = True,
    ) -> DecisionBundle:
        prompt_positions = self._prompt_positions(feature_sheet, positions)
        now = datetime.now(timezone.utc)
        cached = self._playbook_cache
        cache_age = (now - cached.created_at).total_seconds() if cached else None
        provenance = "GROK"
        decision_status = "NOMINAL"
        decision_reasons: list[StructuredReason] = []
        current_symbols = {asset.symbol for asset in feature_sheet.assets}
        if (
            cached is not None
            and {plan.symbol for plan in cached.payload.plans} == current_symbols
            and cache_age is not None
            and cache_age < self.strategist_refresh_seconds
            and not cached.is_expired(now)
        ):
            playbook = cached
            provenance = "CACHE"
            decision_reasons.append(StructuredReason(
                code="STRATEGIST_CACHE_HIT",
                message="The current-universe strategist playbook was reused from cache.",
                impact="NEUTRAL",
                evidence={"cache_age_seconds": round(cache_age, 3)},
            ))
            self._record_skip(cycle_id, "strategist", "CACHE_HIT")
        elif not allow_strategist_refresh and cached is not None:
            provenance = "CACHE"
            cached_by_symbol = {item.symbol: item for item in cached.payload.plans}
            missing_symbols = sorted(current_symbols - set(cached_by_symbol))
            aligned_payload = cached.payload.model_copy(update={"plans": [
                cached_by_symbol.get(asset.symbol, AssetPlan(
                    symbol=asset.symbol,
                    bias="FLAT",
                    conviction=0.0,
                    thesis=(
                        "No cached strategist plan exists for this selected market; "
                        "remain flat until refresh is allowed."
                    ),
                    risk_alloc=0.0,
                ))
                for asset in feature_sheet.assets
            ]})
            playbook = cached.model_copy(update={
                "payload": aligned_payload,
                "feature_sheet_hash": _feature_hash(feature_sheet),
            })
            if missing_symbols:
                decision_status = "DEGRADED"
                decision_reasons.append(StructuredReason(
                    code="CACHE_UNIVERSE_REALIGNED",
                    message="Cached strategist coverage differed from the current universe.",
                    impact="BLOCKS",
                    evidence={"missing_symbols": missing_symbols},
                ))
            else:
                decision_reasons.append(StructuredReason(
                    code="STRATEGIST_REFRESH_DISABLED",
                    message="The current strategist playbook was reused while refresh was disabled.",
                    impact="NEUTRAL",
                    evidence={"cache_age_seconds": round(cache_age or 0.0, 3)},
                ))
            self._record_skip(cycle_id, "strategist", "CAPITAL_CONSTRAINED")
        else:
            strategist_input = {
                "FEATURE_SHEET": feature_sheet.model_dump(mode="json"),
                "ADVISOR_SIGNALS": [s.model_dump(mode="json") for s in signals],
                "EVENT_RESEARCH": research.model_dump(mode="json"),
                "PREVIOUS_PLAYBOOK": (
                    cached.model_dump(mode="json") if cached is not None else None
                ),
                "POSITIONS": [p.model_dump(mode="json") for p in prompt_positions],
                "PORTFOLIO": {
                    "available_collateral_usd": equity_usd,
                    "open_notional_usd": sum(
                        float(p.get("notional_usd", 0)) for p in positions
                    ),
                    "unrealized_pnl_usd": sum(
                        float(p.get("unrealized_pnl_usd", 0)) for p in positions
                    ),
                },
                "NOW_UTC": now.isoformat(),
            }
            strategist_prompt = self._strategist_system_prompt()
            started = time.monotonic()
            strategist_response = None
            try:
                strategist_response = self.client.beta.chat.completions.parse(
                    model=self.model,
                    messages=[
                        {
                            "role": "system",
                            "content": strategist_prompt,
                        },
                        {"role": "user", "content": json.dumps(strategist_input)},
                    ],
                    response_format=PlaybookLLMOutput,
                )
                strategist = strategist_response.choices[0].message.parsed
                if strategist is None:
                    raise RuntimeError(
                        "Grok strategist returned no structured output"
                    )
            except Exception as exc:
                if self.recorder:
                    self.recorder(llm_failure_record(
                        strategist_response,
                        cycle_id=cycle_id,
                        stage="strategist",
                        model=self.model,
                        latency_ms=int((time.monotonic() - started) * 1000),
                        prompt={"system": strategist_prompt, "input": strategist_input},
                        error=exc,
                        reason_code="STRUCTURED_OUTPUT_PARSE_FAILED",
                    ))
                raise
            plans_by_symbol = {item.symbol: item for item in strategist.plans}
            missing_symbols = sorted(current_symbols - set(plans_by_symbol))
            strategist = strategist.model_copy(update={"plans": [
                plans_by_symbol.get(asset.symbol, AssetPlan(
                    symbol=asset.symbol, bias="FLAT", conviction=0.0,
                    thesis="No strategist plan was returned for this selected market; remain flat.",
                    risk_alloc=0.0,
                ))
                for asset in feature_sheet.assets
            ]})
            if missing_symbols:
                decision_status = "DEGRADED"
                decision_reasons.append(StructuredReason(
                    code="INCOMPLETE_STRATEGIST_COVERAGE",
                    message="Missing strategist plans were replaced by fail-closed FLAT plans.",
                    impact="BLOCKS",
                    evidence={"missing_symbols": missing_symbols},
                ))
            if self.recorder:
                self.recorder(llm_record(
                    strategist_response, cycle_id=cycle_id, stage="strategist",
                    model=self.model, latency_ms=int((time.monotonic() - started) * 1000),
                    prompt={"system": strategist_prompt, "input": strategist_input},
                    result=strategist,
                ))
            playbook = PlaybookRecord(
                playbook_id=f"pb-{_feature_hash(feature_sheet)[:16]}",
                version=(cached.version + 1 if cached is not None else 1),
                created_at=now,
                expires_at=now + timedelta(hours=strategist.ttl_hours),
                feature_sheet_hash=_feature_hash(feature_sheet),
                model_id=self.model,
                prompt_version="strategist-v1",
                payload=strategist,
            )
            self._playbook_cache = playbook
        trader_input = {
            "PLAYBOOK": playbook.model_dump(mode="json"),
            "SNAPSHOT": feature_sheet.model_dump(mode="json"),
            "POSITIONS": [p.model_dump(mode="json") for p in prompt_positions],
            "PORTFOLIO": {
                "available_collateral_usd": equity_usd,
                "open_notional_usd": sum(
                    float(p.get("notional_usd", 0)) for p in positions
                ),
                "unrealized_pnl_usd": sum(
                    float(p.get("unrealized_pnl_usd", 0)) for p in positions
                ),
            },
        }
        trader_prompt = self._trader_system_prompt()
        started = time.monotonic()
        trader_response = self.client.beta.chat.completions.parse(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": trader_prompt,
                },
                {"role": "user", "content": json.dumps(trader_input)},
            ],
            response_format=TraderOutput,
        )
        trader = trader_response.choices[0].message.parsed
        if trader is None:
            raise RuntimeError("Grok trader returned no structured output")
        decisions_by_symbol = {item.symbol: item for item in trader.decisions}
        trader = trader.model_copy(update={"decisions": [
            decisions_by_symbol.get(asset.symbol, AssetDecision(
                symbol=asset.symbol, action="HOLD", confidence=0.0,
                rationale="No trader decision was returned for this selected market.",
            ))
            for asset in feature_sheet.assets
        ]})
        if self.recorder:
            self.recorder(llm_record(
                trader_response, cycle_id=cycle_id, stage="trader", model=self.model,
                latency_ms=int((time.monotonic() - started) * 1000),
                prompt={"system": trader_prompt, "input": trader_input}, result=trader,
            ))
        consequences = simulate_consequences(
            trader, playbook, feature_sheet, positions, float(equity_usd or 0)
        )
        risk_increasing = any(item.action == "OPEN" for item in trader.decisions)
        if risk_increasing:
            review_input = {
                "INITIAL_TRADER_PROPOSAL": trader.model_dump(mode="json"),
                "CONSEQUENCE_REPORT": consequences.model_dump(mode="json"),
                "PLAYBOOK": playbook.model_dump(mode="json"),
                "PORTFOLIO": trader_input["PORTFOLIO"],
            }
            started = time.monotonic()
            review_response = self.client.beta.chat.completions.parse(
                model=self.model,
                messages=[
                    {"role": "system", "content": self.risk_review_prompt},
                    {"role": "user", "content": json.dumps(review_input)},
                ],
                response_format=FinalRiskReview,
            )
            review = review_response.choices[0].message.parsed
            if review is None:
                raise RuntimeError("Grok risk review returned no structured output")
            if self.recorder:
                self.recorder(llm_record(
                    review_response, cycle_id=cycle_id, stage="risk_review",
                    model=self.model,
                    latency_ms=int((time.monotonic() - started) * 1000),
                    prompt={"system": self.risk_review_prompt, "input": review_input},
                    result=review,
                ))
        else:
            review = FinalRiskReview(reviews=[
                DecisionRiskReview(
                    symbol=item.symbol,
                    decision="KEEP_AS_IS",
                    reason="No risk-increasing proposal requires consequence revision.",
                )
                for item in trader.decisions
            ])
            self._record_skip(cycle_id, "risk_review", "NO_RISK_INCREASING_PROPOSAL")

        initial_by_symbol = {item.symbol: item for item in trader.decisions}
        reviews_by_symbol = {item.symbol: item for item in review.reviews}
        final_decisions: list[AssetDecision] = []
        for symbol in (item.symbol for item in trader.decisions):
            initial_decision = initial_by_symbol[symbol]
            risk_item = reviews_by_symbol[symbol]
            if risk_item.decision == "KEEP_AS_IS":
                final_decisions.append(initial_decision)
            elif risk_item.decision == "ADJUST" and risk_item.adjusted_decision:
                final_decisions.append(risk_item.adjusted_decision)
            else:
                final_decisions.append(AssetDecision(
                    symbol=symbol,
                    action="HOLD",
                    confidence=initial_decision.confidence,
                    rationale=f"Final consequence review canceled proposal: {risk_item.reason}"[:300],
                ))
        final_trader = trader.model_copy(update={"decisions": final_decisions})
        return DecisionBundle(
            playbook=playbook,
            trader=final_trader,
            initial_trader=trader,
            consequence_report=consequences,
            risk_review=review,
            provider=self.model,
            provenance=provenance,
            status=decision_status,
            reasons=decision_reasons,
            conviction_diagnostics=build_conviction_diagnostics(
                playbook,
                signals,
                research,
                self.config.min_plan_conviction,
            ),
        )

    def _record_skip(self, cycle_id: str | None, stage: str, reason: str) -> None:
        if self.recorder:
            self.recorder({"cycle_id": cycle_id, "stage": stage, "provider": "xai",
                           "model": self.model, "status": "SKIPPED",
                           "skipped_reason": reason})

    @staticmethod
    def _prompt_positions(
        feature_sheet: FeatureSheet, positions: list[dict]
    ) -> list[PromptPosition]:
        assets = {asset.symbol: asset for asset in feature_sheet.assets}
        projected: list[PromptPosition] = []
        for position in positions:
            asset = assets[position["symbol"]]
            entry = float(position["entry_px"])
            invalidation = float(position["invalidation_px"])
            mark = asset.mark_px
            if position["side"] == "LONG":
                risk_distance = max(entry - invalidation, 1e-9)
                unrealized_r = (mark - entry) / risk_distance
            else:
                risk_distance = max(invalidation - entry, 1e-9)
                unrealized_r = (entry - mark) / risk_distance
            projected.append(
                PromptPosition(
                    symbol=position["symbol"],
                    side=position["side"],
                    entry_px=entry,
                    mark_px=mark,
                    invalidation_px=invalidation,
                    notional_usd=float(position["notional_usd"]),
                    leverage=int(position.get("leverage", 1)),
                    unrealized_pnl_usd=float(
                        position.get("unrealized_pnl_usd", 0)
                    ),
                    roe_pct=float(position.get("roe_pct", 0)),
                    unrealized_r=unrealized_r,
                    distance_to_invalidation_atr=abs(mark - invalidation) / asset.atr_4h,
                )
            )
        return projected
