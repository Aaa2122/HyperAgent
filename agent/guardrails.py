from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from agent.domain import ApprovedOrder, DecisionBundle, GuardrailVerdict
from llm_checks import (
    AssetSnapshot,
    LLMLayerConfig,
    PortfolioContext,
    PositionState,
    check_decision,
    check_plan_against_market,
    size_open_order,
)
from llm_schemas import FeatureSheet


class GuardrailEngine:
    def __init__(self, config: LLMLayerConfig | None = None):
        self.config = config or LLMLayerConfig()

    def evaluate(
        self,
        cycle_id: str,
        feature_sheet: FeatureSheet,
        decision_bundle: DecisionBundle,
        positions: list[dict],
        equity_usd: float,
    ) -> tuple[list[GuardrailVerdict], list[ApprovedOrder]]:
        snapshots = {
            asset.symbol: AssetSnapshot(
                symbol=asset.symbol,
                mark_px=asset.mark_px,
                atr_4h=asset.atr_4h,
                spread_bps=asset.spread_bps,
                data_age_seconds=asset.data_age_seconds,
                max_leverage=asset.max_leverage,
            )
            for asset in feature_sheet.assets
        }
        portfolio = PortfolioContext(
            equity_usd=equity_usd,
            positions=[
                PositionState(
                    symbol=p["symbol"],
                    side=p["side"],
                    notional_usd=p["notional_usd"],
                    entry_px=p["entry_px"],
                    invalidation_px=p["invalidation_px"],
                )
                for p in positions
            ],
        )
        positions_by_symbol = {p["symbol"]: p for p in positions}
        verdicts: list[GuardrailVerdict] = []
        approved: list[ApprovedOrder] = []

        for decision in decision_bundle.trader.decisions:
            plan = decision_bundle.playbook.payload.plan_for(decision.symbol)
            snap = snapshots[decision.symbol]
            if decision.action == "HOLD":
                verdicts.append(
                    GuardrailVerdict(
                        symbol=decision.symbol,
                        action="HOLD",
                        verdict="SKIP",
                        reasons=["HOLD"],
                    )
                )
                continue

            violations = []
            if not (decision.symbol == plan.symbol == snap.symbol):
                violations.append("SYMBOL_CONTEXT_MISMATCH")
            persisted_plan = decision_bundle.playbook.payload.plan_for(decision.symbol)
            if persisted_plan != plan:
                violations.append("PLAYBOOK_PLAN_MISMATCH")
            violations.extend(
                v.code
                for v in check_decision(
                    decision,
                    plan,
                    decision_bundle.playbook,
                    snap,
                    portfolio,
                    self.config,
                    datetime.now(timezone.utc),
                )
            )
            if decision.action == "OPEN" and not self.config.operational_only:
                violations.extend(
                    v.code for v in check_plan_against_market(plan, snap, self.config)
                )
            if violations:
                verdicts.append(
                    GuardrailVerdict(
                        symbol=decision.symbol,
                        action=decision.action,
                        verdict="REJECT",
                        reasons=sorted(set(violations)),
                    )
                )
                continue

            if decision.action == "OPEN":
                sized = size_open_order(decision, plan, snap, portfolio, self.config)
                if sized.notional_usd == 0:
                    verdicts.append(
                        GuardrailVerdict(
                            symbol=decision.symbol,
                            action="OPEN",
                            verdict="REJECT",
                            reasons=sized.cap_reasons,
                        )
                    )
                    continue
                notional = sized.notional_usd
                direction = decision.direction
                invalidation = plan.invalidation_px
                targets = list(plan.targets)
                leverage = min(
                    decision.leverage,
                    self.config.max_leverage,
                    snap.max_leverage,
                )
                reasons = list(sized.cap_reasons)
                if leverage != decision.leverage:
                    reasons.append("VENUE_LEVERAGE_CAP")
                verdict = "MODIFY" if reasons else "APPROVE"
            else:
                position = positions_by_symbol[decision.symbol]
                notional = position["notional_usd"]
                if decision.action == "REDUCE":
                    notional *= decision.size_frac
                direction = position["side"]
                invalidation = position["invalidation_px"]
                targets = list(position.get("targets", []))
                leverage = int(position.get("leverage", 1))
                verdict, reasons = "APPROVE", []

            if direction is None or invalidation is None:
                raise RuntimeError("guardrail produced an incomplete approved order")
            decision_payload = {
                "cycle_id": cycle_id,
                "playbook_id": decision_bundle.playbook.playbook_id,
                "symbol": decision.symbol,
                "action": decision.action,
                "direction": direction,
                "notional_usd": round(notional, 8),
                "mark_px": round(snap.mark_px, 8),
                "order_type": decision.order_type,
                "limit_px": decision.limit_px,
                "invalidation_px": round(invalidation, 8),
                "targets": [round(target, 8) for target in targets],
                "place_stop_order": decision.place_stop_order,
                "take_profit_fractions": list(decision.take_profit_fractions),
                "exit_management": decision.exit_management,
                "trailing_stop_pct": decision.trailing_stop_pct,
                "time_stop_hours": decision.time_stop_hours,
                "move_to_break_even_at_r": decision.move_to_break_even_at_r,
                "leverage": leverage,
            }
            decision_key = hashlib.sha256(
                json.dumps(decision_payload, sort_keys=True).encode()
            ).hexdigest()
            approved.append(
                ApprovedOrder(
                    **decision_payload,
                    decision_key=decision_key,
                )
            )
            verdicts.append(
                GuardrailVerdict(
                    symbol=decision.symbol,
                    action=decision.action,
                    verdict=verdict,
                    reasons=reasons,
                    notional_usd=notional,
                    leverage=leverage,
                )
            )
        return verdicts, approved
