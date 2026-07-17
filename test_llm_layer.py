"""
Tests de la couche décision LLM (AC-S1).
Exécutable sans pytest : `python3 test_llm_layer.py`.
Compatible pytest également (fonctions test_*).
"""

from __future__ import annotations

import copy
import sys
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from llm_checks import (
    AssetSnapshot,
    LLMLayerConfig,
    PortfolioContext,
    PositionState,
    check_decision,
    check_plan_against_market,
    size_open_order,
)
from llm_schemas import (
    AssetDecision,
    AssetPlan,
    FeatureSheet,
    PlaybookLLMOutput,
    PlaybookRecord,
    TraderOutput,
)

PASSED = 0


def ok(label: str, cond: bool) -> None:
    global PASSED
    assert cond, f"FAIL: {label}"
    PASSED += 1
    print(f"  ok - {label}")


def rejected(label: str, fn) -> None:
    try:
        fn()
    except ValidationError:
        ok(label, True)
        return
    raise AssertionError(f"FAIL (aurait dû être rejeté au parsing): {label}")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def base_plans() -> list[dict]:
    return [
        dict(
            symbol="BTC",
            bias="LONG",
            conviction=0.72,
            thesis="4h uptrend: ADX 31, Donchian pos 0.86, price 1.4 ATR above "
            "EMA20; funding mildly positive at 0.008%/h.",
            entry_zone=(49200.0, 50100.0),
            invalidation_px=48400.0,
            targets=[51800.0, 53500.0],
            risk_alloc=0.5,
        ),
        dict(
            symbol="ETH",
            bias="FLAT",
            conviction=0.3,
            thesis="Mixed: ADX 14, mid-Donchian 0.48, ETH/BTC 7d flat; no "
            "structural edge either side.",
            risk_alloc=0.0,
        ),
        dict(
            symbol="SOL",
            bias="SHORT",
            conviction=0.6,
            thesis="Rejection at range high: Donchian 0.93 then reversal, "
            "funding stretched 0.045%/h, OI +18% in 24h (crowded longs).",
            entry_zone=(152.0, 158.0),
            invalidation_px=164.5,
            targets=[141.0, 133.0],
            risk_alloc=0.35,
        ),
    ]


def valid_playbook_dict() -> dict:
    return dict(
        regime_view="BTC trending up on 4h, ETH rangebound, SOL overextended "
        "at range highs with crowded longs.",
        plans=base_plans(),
        changes_vs_previous="First playbook of the run; no previous version to diff against.",
        ttl_hours=8,
    )


def valid_trader_dict() -> dict:
    return dict(
        decisions=[
            dict(
                symbol="BTC",
                action="OPEN",
                direction="LONG",
                size_frac=0.75,
                confidence=0.7,
                rationale="Mark 49800 inside 49200-50100 zone, stop 1.75 ATR "
                "below, funding benign.",
            ),
            dict(
                symbol="ETH",
                action="HOLD",
                confidence=0.4,
                rationale="FLAT plan and no position; nothing to do.",
            ),
            dict(
                symbol="SOL",
                action="HOLD",
                confidence=0.5,
                rationale="Price below entry zone 152-158; no valid setup this cycle.",
            ),
        ],
    )


NOW = datetime(2026, 7, 8, 12, 0, tzinfo=timezone.utc)


def make_record(pb: PlaybookLLMOutput, expired: bool = False) -> PlaybookRecord:
    return PlaybookRecord(
        playbook_id="pb-0001",
        version=1,
        created_at=NOW - timedelta(hours=9 if expired else 1),
        expires_at=NOW - timedelta(minutes=1) if expired else NOW + timedelta(hours=7),
        feature_sheet_hash="a" * 64,
        model_id="frontier-x",
        prompt_version="strategist-v1",
        payload=pb,
    )


def mutate_playbook(plan_patches: dict) -> dict:
    """plan_patches: {index: {champ: valeur}}"""
    d = copy.deepcopy(valid_playbook_dict())
    for idx, patch in plan_patches.items():
        d["plans"][idx].update(patch)
    return d


# ---------------------------------------------------------------------------
# 1. Validation structurelle — playbook
# ---------------------------------------------------------------------------


def test_structural_playbook() -> None:
    print("\n[1] Structurel — playbook")
    pb = PlaybookLLMOutput(**valid_playbook_dict())
    ok("playbook valide parse", pb.ttl_hours == 8)
    ok("plan_for retrouve SOL", pb.plan_for("SOL").bias == "SHORT")

    rejected(
        "FLAT avec targets",
        lambda: PlaybookLLMOutput(**mutate_playbook({1: {"targets": [3000.0]}})),
    )
    rejected(
        "FLAT avec risk_alloc > 0",
        lambda: PlaybookLLMOutput(**mutate_playbook({1: {"risk_alloc": 0.2}})),
    )
    rejected(
        "LONG invalidation dans/au-dessus de la zone",
        lambda: PlaybookLLMOutput(**mutate_playbook({0: {"invalidation_px": 49500.0}})),
    )
    rejected(
        "SHORT invalidation sous la zone",
        lambda: PlaybookLLMOutput(**mutate_playbook({2: {"invalidation_px": 150.0}})),
    )
    rejected(
        "targets mal ordonnés (LONG)",
        lambda: PlaybookLLMOutput(**mutate_playbook({0: {"targets": [53500.0, 51800.0]}})),
    )
    rejected(
        "somme risk_alloc > 1",
        lambda: PlaybookLLMOutput(**mutate_playbook({0: {"risk_alloc": 0.8}})),
    )
    rejected(
        "couverture cassée (BTC dupliqué)",
        lambda: PlaybookLLMOutput(**mutate_playbook({2: {"symbol": "BTC"}})),
    )
    rejected(
        "symbol hors whitelist",
        lambda: PlaybookLLMOutput(**mutate_playbook({2: {"symbol": "DOGE"}})),
    )
    rejected(
        "ttl_hours hors bornes",
        lambda: PlaybookLLMOutput(**{**valid_playbook_dict(), "ttl_hours": 24}),
    )
    rejected(
        "champ inconnu (extra=forbid)",
        lambda: PlaybookLLMOutput(**{**valid_playbook_dict(), "leverage": 10}),
    )
    rejected(
        "thesis trop courte",
        lambda: PlaybookLLMOutput(**mutate_playbook({0: {"thesis": "too short"}})),
    )
    rejected(
        "plan non-FLAT sans invalidation",
        lambda: AssetPlan(
            symbol="BTC",
            bias="LONG",
            conviction=0.6,
            thesis="Long thesis without any invalidation level provided at all.",
            risk_alloc=0.3,
        ),
    )


# ---------------------------------------------------------------------------
# 2. Validation structurelle — trader
# ---------------------------------------------------------------------------


def test_structural_trader() -> None:
    print("\n[2] Structurel — trader")
    to = TraderOutput(**valid_trader_dict())
    ok("sortie trader valide parse", to.decision_for("BTC").action == "OPEN")

    def patch(idx: int, **kw) -> dict:
        d = copy.deepcopy(valid_trader_dict())
        d["decisions"][idx].update(kw)
        return d

    rejected("OPEN sans direction", lambda: TraderOutput(**patch(0, direction=None)))
    rejected("OPEN avec size_frac = 0", lambda: TraderOutput(**patch(0, size_frac=0.0)))
    rejected(
        "CLOSE avec size_frac > 0", lambda: TraderOutput(**patch(1, action="CLOSE", size_frac=0.5))
    )
    rejected(
        "REDUCE avec direction",
        lambda: TraderOutput(**patch(1, action="REDUCE", size_frac=0.5, direction="LONG")),
    )
    rejected("HOLD avec size_frac > 0", lambda: TraderOutput(**patch(2, size_frac=0.3)))
    rejected(
        "review demandé sans raison",
        lambda: TraderOutput(**{**valid_trader_dict(), "request_strategist_review": True}),
    )
    rejected(
        "raison sans flag de review",
        lambda: TraderOutput(
            **{**valid_trader_dict(), "review_reason": "Funding flipped hard on SOL."}
        ),
    )
    rejected("actif manquant (BTC dupliqué)", lambda: TraderOutput(**patch(1, symbol="BTC")))


# ---------------------------------------------------------------------------
# 3. Contextuel — plan vs marché
# ---------------------------------------------------------------------------


def test_contextual_plan() -> None:
    print("\n[3] Contextuel — plan vs marché")
    cfg = LLMLayerConfig()
    pb = PlaybookLLMOutput(**valid_playbook_dict())
    snap = AssetSnapshot(
        symbol="BTC", mark_px=49800.0, atr_4h=800.0, spread_bps=1.2, data_age_seconds=5.0
    )

    ok(
        "plan BTC cohérent avec le marché → aucune violation",
        check_plan_against_market(pb.plan_for("BTC"), snap, cfg) == [],
    )

    wrong_side = AssetPlan(
        symbol="BTC",
        bias="LONG",
        conviction=0.6,
        thesis="Long idea with a stop incorrectly placed above the current "
        "market price for this test.",
        invalidation_px=50500.0,
        risk_alloc=0.3,
    )
    codes = [x.code for x in check_plan_against_market(wrong_side, snap, cfg)]
    ok("invalidation du mauvais côté détectée", "INVALIDATION_WRONG_SIDE" in codes)

    too_wide = AssetPlan(
        symbol="BTC",
        bias="LONG",
        conviction=0.6,
        thesis="Long idea with an absurdly wide stop, several ATR away from "
        "the market, for this test.",
        invalidation_px=44000.0,
        risk_alloc=0.3,
    )
    codes = [x.code for x in check_plan_against_market(too_wide, snap, cfg)]
    ok("stop trop large détecté (7.25 ATR)", "INVALIDATION_TOO_WIDE" in codes)

    behind = AssetPlan(
        symbol="BTC",
        bias="LONG",
        conviction=0.6,
        thesis="Long idea whose first target is already below the current "
        "market price for this test.",
        invalidation_px=48400.0,
        targets=[49000.0, 52000.0],
        risk_alloc=0.3,
    )
    codes = [x.code for x in check_plan_against_market(behind, snap, cfg)]
    ok("target déjà dépassé détecté", "TARGETS_BEHIND_PRICE" in codes)


# ---------------------------------------------------------------------------
# 4. Contextuel — décisions du trader
# ---------------------------------------------------------------------------


def test_contextual_decision() -> None:
    print("\n[4] Contextuel — décisions")
    cfg = LLMLayerConfig()
    pb = PlaybookLLMOutput(**valid_playbook_dict())
    plan_btc = pb.plan_for("BTC")
    record = make_record(pb)
    snap = AssetSnapshot(
        symbol="BTC", mark_px=49800.0, atr_4h=800.0, spread_bps=1.2, data_age_seconds=5.0
    )
    pf_empty = PortfolioContext(equity_usd=10_000.0)
    pf_with_pos = PortfolioContext(
        equity_usd=10_000.0,
        positions=[
            PositionState(
                symbol="BTC",
                side="LONG",
                notional_usd=1_500.0,
                entry_px=49_500.0,
                invalidation_px=48_400.0,
            )
        ],
    )

    d_open = AssetDecision(
        symbol="BTC",
        action="OPEN",
        direction="LONG",
        size_frac=0.75,
        confidence=0.7,
        rationale="Inside entry zone, stop 1.75 ATR below, funding benign.",
    )
    ok(
        "OPEN conforme → aucune violation",
        check_decision(d_open, plan_btc, record, snap, pf_empty, cfg, NOW) == [],
    )

    codes = [
        x.code
        for x in check_decision(
            d_open, plan_btc, make_record(pb, expired=True), snap, pf_empty, cfg, NOW
        )
    ]
    ok("playbook expiré bloque OPEN", "PLAYBOOK_EXPIRED" in codes)

    d_short = AssetDecision(
        symbol="BTC",
        action="OPEN",
        direction="SHORT",
        size_frac=0.5,
        confidence=0.8,
        rationale="Deliberate mismatch against the playbook bias for this test.",
    )
    codes = [x.code for x in check_decision(d_short, plan_btc, record, snap, pf_empty, cfg, NOW)]
    ok("OPEN contre le bias bloqué", "BIAS_MISMATCH" in codes)

    d_flat = AssetDecision(
        symbol="ETH",
        action="OPEN",
        direction="LONG",
        size_frac=0.5,
        confidence=0.8,
        rationale="Attempt to open on a FLAT plan for this test case.",
    )
    snap_eth = AssetSnapshot(
        symbol="ETH", mark_px=3_000.0, atr_4h=60.0, spread_bps=1.5, data_age_seconds=5.0
    )
    codes = [
        x.code
        for x in check_decision(d_flat, pb.plan_for("ETH"), record, snap_eth, pf_empty, cfg, NOW)
    ]
    ok("OPEN sur plan FLAT bloqué", "BIAS_FLAT" in codes)

    codes = [x.code for x in check_decision(d_open, plan_btc, record, snap, pf_with_pos, cfg, NOW)]
    ok("OPEN avec position déjà ouverte bloqué", "ALREADY_IN_POSITION" in codes)

    snap_stale = AssetSnapshot(
        symbol="BTC", mark_px=49800.0, atr_4h=800.0, spread_bps=1.2, data_age_seconds=120.0
    )
    codes = [
        x.code for x in check_decision(d_open, plan_btc, record, snap_stale, pf_empty, cfg, NOW)
    ]
    ok("données périmées bloquent OPEN (I4)", "DATA_STALE" in codes)

    d_close = AssetDecision(
        symbol="BTC",
        action="CLOSE",
        confidence=0.9,
        rationale="Exiting the full position after target touch in this test.",
    )
    ok(
        "CLOSE passe même sur données périmées (asymétrie voulue)",
        check_decision(d_close, plan_btc, record, snap_stale, pf_with_pos, cfg, NOW) == [],
    )
    codes = [x.code for x in check_decision(d_close, plan_btc, record, snap, pf_empty, cfg, NOW)]
    ok("CLOSE sans position bloqué", "NO_POSITION" in codes)

    snap_far = AssetSnapshot(
        symbol="BTC", mark_px=51_000.0, atr_4h=800.0, spread_bps=1.2, data_age_seconds=5.0
    )
    codes = [x.code for x in check_decision(d_open, plan_btc, record, snap_far, pf_empty, cfg, NOW)]
    ok("prix hors zone d'entrée bloqué", "OUT_OF_ENTRY_ZONE" in codes)

    d_shy = AssetDecision(
        symbol="BTC",
        action="OPEN",
        direction="LONG",
        size_frac=0.5,
        confidence=0.4,
        rationale="Low-confidence open attempt for this test case.",
    )
    codes = [x.code for x in check_decision(d_shy, plan_btc, record, snap, pf_empty, cfg, NOW)]
    ok("confidence sous le seuil bloquée", "LOW_CONFIDENCE" in codes)

    pf_busy = PortfolioContext(equity_usd=10_000.0, opens_today={"BTC": 3})
    codes = [x.code for x in check_decision(d_open, plan_btc, record, snap, pf_busy, cfg, NOW)]
    ok("limite d'OPEN journalière bloquée", "OVERTRADE_LIMIT" in codes)

    pf_cool = PortfolioContext(equity_usd=10_000.0, minutes_since_stop_out={"BTC": 30.0})
    codes = [x.code for x in check_decision(d_open, plan_btc, record, snap, pf_cool, cfg, NOW)]
    ok("cooldown post stop-out bloqué", "STOP_OUT_COOLDOWN" in codes)


# ---------------------------------------------------------------------------
# 5. Sizing déterministe
# ---------------------------------------------------------------------------


def test_sizing() -> None:
    print("\n[5] Sizing from margin allocation")
    cfg = LLMLayerConfig()
    plan = AssetPlan(
        symbol="BTC",
        bias="LONG",
        conviction=0.7,
        thesis="Reference long plan used for the numeric sizing example of the annex document.",
        invalidation_px=49_000.0,
        risk_alloc=0.5,
    )
    snap = AssetSnapshot(
        symbol="BTC", mark_px=50_000.0, atr_4h=800.0, spread_bps=1.0, data_age_seconds=3.0
    )
    d = AssetDecision(
        symbol="BTC",
        action="OPEN",
        direction="LONG",
        size_frac=1.0,
        confidence=0.8,
        rationale="Full deployment of the planned risk for the sizing example.",
    )

    pf = PortfolioContext(equity_usd=10_000.0)
    so = size_open_order(d, plan, snap, pf, cfg)
    # marge = 100% × 10000 = 10000 $ ; levier 1× → notionnel 10000 $
    # risque avant caps = 10000 × (1000 / 50000) = 200 $, puis cap actif à 2000 $.
    ok("risque avant caps calculé = 200 $", abs(so.risk_usd - 200.0) < 1e-6)
    ok("notionnel cappé au cap actif (2000 $)", abs(so.notional_usd - 2_000.0) < 1e-6)
    ok(
        "raison ASSET_NOTIONAL_CAP présente",
        "ASSET_NOTIONAL_CAP" in so.cap_reasons and so.was_capped,
    )

    pf_loaded = PortfolioContext(
        equity_usd=10_000.0,
        positions=[
            PositionState(
                symbol="ETH",
                side="LONG",
                notional_usd=5_500.0,
                entry_px=3_000.0,
                invalidation_px=2_850.0,
            )
        ],
    )
    so2 = size_open_order(d, plan, snap, pf_loaded, cfg)
    # cap net = 60% × 10000 = 6000 $ ; net actuel +5500 → headroom 500 $
    ok("cap d'exposition nette applique le headroom (500 $)", abs(so2.notional_usd - 500.0) < 1e-6)
    ok("raison NET_EXPOSURE_CAP présente", "NET_EXPOSURE_CAP" in so2.cap_reasons)

    d_tiny = AssetDecision(
        symbol="BTC",
        action="OPEN",
        direction="LONG",
        size_frac=0.002,
        confidence=0.8,
        rationale="Tiny deployment to trigger the minimum notional floor.",
    )
    so3 = size_open_order(d_tiny, plan, snap, pf, cfg)
    # allocation 0.2% → notionnel 20 $ < plancher 25 $ → 0 (REJECT)
    ok(
        "sous le plancher → notionnel 0 (REJECT)",
        so3.notional_usd == 0.0 and "MIN_NOTIONAL" in so3.cap_reasons,
    )


# ---------------------------------------------------------------------------
# 6. Feature sheet (smoke test)
# ---------------------------------------------------------------------------


def make_features(symbol: str, mark: float) -> dict:
    return dict(
        symbol=symbol,
        mark_px=mark,
        ret_1h_pct=0.2,
        ret_4h_pct=0.9,
        ret_1d_pct=2.1,
        ret_7d_pct=5.4,
        atr_4h=mark * 0.016,
        adx_4h=27.0,
        donchian_pos_4h=0.82,
        dist_ema20_4h_atr=1.3,
        dist_ema200_1d_atr=2.9,
        rv_24h_ann_pct=41.0,
        rv_7d_ann_pct=38.0,
        funding_1h_pct=0.008,
        funding_24h_avg_pct=0.006,
        oi_usd=1.9e9,
        oi_change_24h_pct=6.0,
        liq_longs_24h_usd=1.1e7,
        liq_shorts_24h_usd=2.3e7,
        swing_high_4h=mark * 1.03,
        swing_low_4h=mark * 0.96,
        vwap_1d=mark * 0.995,
        spread_bps=1.1,
        data_age_seconds=4.0,
        advisors=[
            dict(
                strategy_id="naive_momentum",
                direction="LONG",
                conviction=0.65,
                features={"donchian_break_atr": 0.8},
            )
        ],
    )


def test_feature_sheet() -> None:
    print("\n[6] Feature sheet")
    fs = FeatureSheet(
        as_of=NOW,
        assets=[
            make_features("BTC", 50_000.0),
            make_features("ETH", 3_000.0),
            make_features("SOL", 155.0),
        ],
        corr_30d_btc_eth=0.87,
        corr_30d_btc_sol=0.74,
    )
    ok("feature sheet valide parse (3 actifs, advisors inclus)", len(fs.assets) == 3)
    rejected(
        "feature sheet incomplet rejeté",
        lambda: FeatureSheet(
            as_of=NOW,
            assets=[
                make_features("BTC", 50_000.0),
                make_features("BTC", 50_000.0),
                make_features("SOL", 155.0),
            ],
            corr_30d_btc_eth=0.87,
            corr_30d_btc_sol=0.74,
        ),
    )


if __name__ == "__main__":
    # Windows may otherwise select cp1252 when this script is run from a
    # redirected CI shell, which cannot print labels containing arrows.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    test_structural_playbook()
    test_structural_trader()
    test_contextual_plan()
    test_contextual_decision()
    test_sizing()
    test_feature_sheet()
    print(f"\nALL TESTS PASSED ({PASSED} checks)")


def test_flat_plan_normalizes_zero_invalidation_without_weakening_directional_plans():
    flat = AssetPlan(
        symbol="ETH",
        bias="FLAT",
        conviction=0.2,
        thesis="No directional edge is present in the current market regime.",
        invalidation_px=0,
        risk_alloc=0,
    )
    assert flat.invalidation_px is None
    with pytest.raises(ValidationError, match="invalidation_px obligatoire"):
        AssetPlan(
            symbol="BTC",
            bias="LONG",
            conviction=0.7,
            thesis="Directional setup with deliberately invalid zero stop price.",
            invalidation_px=0,
            risk_alloc=0.3,
        )
