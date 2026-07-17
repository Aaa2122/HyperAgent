from __future__ import annotations

from datetime import datetime, timezone

from llm_schemas import (
    ConsequenceReport,
    ConsequenceScenario,
    DecisionConsequences,
    FeatureSheet,
    PlaybookRecord,
    TraderOutput,
)


def simulate_consequences(
    trader: TraderOutput,
    playbook: PlaybookRecord,
    feature_sheet: FeatureSheet,
    positions: list[dict],
    equity_usd: float,
    *,
    round_trip_fee_rate: float = 0.0009,
) -> ConsequenceReport:
    """Describe consequences without recommending, scoring, resizing, or vetoing."""
    assets = {asset.symbol: asset for asset in feature_sheet.assets}
    gross_before = sum(float(item.get("notional_usd", 0)) for item in positions)
    net_before = sum(
        float(item.get("notional_usd", 0)) * (1 if item.get("side") == "LONG" else -1)
        for item in positions
    )
    reports: list[DecisionConsequences] = []
    for decision in trader.decisions:
        asset = assets[decision.symbol]
        plan = playbook.payload.plan_for(decision.symbol)
        if decision.action != "OPEN" or decision.direction is None:
            reports.append(
                DecisionConsequences(
                    symbol=decision.symbol,
                    action=decision.action,
                    assumptions={"market_as_of": feature_sheet.as_of.isoformat()},
                    operational_facts={"risk_increasing": False},
                )
            )
            continue

        notional = float(
            decision.notional_usd
            if decision.notional_usd is not None
            else equity_usd * decision.size_frac * decision.leverage
        )
        leverage = max(1, decision.leverage)
        stop = float(plan.invalidation_px or asset.mark_px)
        stop_distance = abs(asset.mark_px - stop)
        quantity = notional / asset.mark_px if asset.mark_px else 0
        stop_loss = quantity * stop_distance
        margin = notional / leverage
        long = decision.direction == "LONG"
        liquidation = (
            asset.mark_px * (1 - 1 / leverage) if long else asset.mark_px * (1 + 1 / leverage)
        )
        liq_to_stop_atr = abs(liquidation - stop) / max(asset.atr_4h, 1e-12)
        funding_cost = (
            notional * (asset.funding_1h_pct / 100) * decision.horizon_hours * (1 if long else -1)
        )
        slippage = notional * asset.spread_bps / 10_000 * 2
        fee = notional * round_trip_fee_rate
        signed = 1 if long else -1
        scenarios = []
        for multiplier in (0.5, 1.0, 1.5):
            scenario_notional = notional * multiplier
            scenario_qty = scenario_notional / asset.mark_px if asset.mark_px else 0
            scenarios.append(
                ConsequenceScenario(
                    size_multiplier=multiplier,
                    notional_usd=scenario_notional,
                    stop_loss_usd=scenario_qty * stop_distance,
                    stop_loss_equity_pct=(scenario_qty * stop_distance / max(equity_usd, 1e-12))
                    * 100,
                    margin_used_usd=scenario_notional / leverage,
                    funding_estimate_usd=funding_cost * multiplier,
                    fees_estimate_usd=fee * multiplier,
                    slippage_estimate_usd=slippage * multiplier,
                )
            )
        liquidation_before_stop = liquidation >= stop if long else liquidation <= stop
        reports.append(
            DecisionConsequences(
                symbol=decision.symbol,
                action=decision.action,
                assumptions={
                    "market_as_of": feature_sheet.as_of.isoformat(),
                    "mark_px": asset.mark_px,
                    "stop_px": stop,
                    "atr_4h": asset.atr_4h,
                    "funding_1h_pct": asset.funding_1h_pct,
                    "horizon_hours": decision.horizon_hours,
                    "spread_bps_used_for_slippage": asset.spread_bps,
                    "round_trip_fee_rate": round_trip_fee_rate,
                    "liquidation_model": "linear approximation; venue result may differ",
                },
                proposed_notional_usd=notional,
                stop_loss_usd=stop_loss,
                stop_loss_equity_pct=stop_loss / max(equity_usd, 1e-12) * 100,
                margin_used_usd=margin,
                liquidation_px_estimate=liquidation,
                liquidation_to_stop_atr=liq_to_stop_atr,
                funding_estimate_usd=funding_cost,
                fees_estimate_usd=fee,
                slippage_estimate_usd=slippage,
                gross_exposure_after_usd=gross_before + notional,
                net_exposure_after_usd=net_before + signed * notional,
                adverse_move_1atr_usd=quantity * asset.atr_4h,
                adverse_move_2atr_usd=quantity * asset.atr_4h * 2,
                adverse_move_3atr_usd=quantity * asset.atr_4h * 3,
                scenarios=scenarios,
                operational_facts={
                    "notional_was_explicit": decision.notional_usd is not None,
                    "margin_within_available_collateral": margin <= equity_usd,
                    "liquidation_before_stop": liquidation_before_stop,
                    "data_age_seconds": asset.data_age_seconds,
                    "venue_max_leverage": asset.max_leverage,
                    "requested_leverage_within_venue_limit": leverage <= asset.max_leverage,
                },
            )
        )
    return ConsequenceReport(
        as_of=datetime.now(timezone.utc),
        disclaimer=(
            "Neutral consequence calculation. Figures only; no advice, criticism, "
            "danger score, preferred size, or strategic verdict."
        ),
        decisions=reports,
    )
