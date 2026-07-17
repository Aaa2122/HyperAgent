import test_llm_layer as fixtures
from agent.consequence import simulate_consequences
from llm_checks import AssetSnapshot, LLMLayerConfig, PortfolioContext, size_open_order
from llm_schemas import AssetDecision, FeatureSheet, PlaybookLLMOutput, TraderOutput


def test_model_explicit_notional_is_preserved_by_sizing() -> None:
    plan = PlaybookLLMOutput(**fixtures.valid_playbook_dict()).plan_for("BTC")
    decision = AssetDecision(
        symbol="BTC",
        action="OPEN",
        direction="LONG",
        size_frac=1,
        leverage=4,
        notional_usd=12_345,
        confidence=0.8,
        rationale="Explicit discretionary allocation selected from the complete portfolio context.",
    )
    sized = size_open_order(
        decision,
        plan,
        AssetSnapshot(symbol="BTC", mark_px=50_000, atr_4h=800, spread_bps=1, data_age_seconds=2),
        PortfolioContext(equity_usd=20_000, positions=[]),
        LLMLayerConfig(
            max_asset_notional_usd={"BTC": 1e9, "ETH": 1e9, "SOL": 1e9}, max_net_exposure_frac=1e9
        ),
    )
    assert sized.notional_usd == 12_345
    assert not sized.was_capped


def test_consequence_report_is_descriptive_and_counterfactual() -> None:
    playbook_payload = PlaybookLLMOutput(**fixtures.valid_playbook_dict())
    playbook = fixtures.make_record(playbook_payload)
    trader_data = fixtures.valid_trader_dict()
    trader_data["decisions"][0].update(notional_usd=10_000, leverage=5, horizon_hours=12)
    trader = TraderOutput(**trader_data)
    sheet = FeatureSheet(
        as_of=fixtures.NOW,
        assets=[
            fixtures.make_features("BTC", 50_000),
            fixtures.make_features("ETH", 3_000),
            fixtures.make_features("SOL", 155),
        ],
        corr_30d_btc_eth=0.87,
        corr_30d_btc_sol=0.74,
    )
    report = simulate_consequences(trader, playbook, sheet, [], equity_usd=20_000)
    btc = next(item for item in report.decisions if item.symbol == "BTC")
    assert btc.proposed_notional_usd == 10_000
    assert btc.margin_used_usd == 2_000
    assert [item.size_multiplier for item in btc.scenarios] == [0.5, 1, 1.5]
    assert btc.stop_loss_equity_pct > 0
    assert "recommend" not in report.disclaimer.lower()
    assert not hasattr(btc, "recommended_size")


def test_operational_mode_ignores_strategic_confidence_but_requires_notional() -> None:
    plan = PlaybookLLMOutput(**fixtures.valid_playbook_dict()).plan_for("BTC")
    snapshot = AssetSnapshot(
        symbol="BTC", mark_px=49_800, atr_4h=800, spread_bps=1, data_age_seconds=2, max_leverage=20
    )
    portfolio = PortfolioContext(equity_usd=10_000, positions=[])
    cfg = LLMLayerConfig(operational_only=True)
    no_notional = AssetDecision(
        symbol="BTC",
        action="OPEN",
        direction="LONG",
        size_frac=0.2,
        confidence=0,
        rationale="Low conviction is still a discretionary strategic choice here.",
    )
    from llm_checks import check_decision

    playbook = fixtures.make_record(PlaybookLLMOutput(**fixtures.valid_playbook_dict()))
    assert "NOTIONAL_NOT_EXPLICIT" in {
        item.code
        for item in check_decision(
            no_notional, plan, playbook, snapshot, portfolio, cfg, fixtures.NOW
        )
    }
    explicit = no_notional.model_copy(update={"notional_usd": 1_000})
    assert "LOW_OPEN_CONFIDENCE" not in {
        item.code
        for item in check_decision(explicit, plan, playbook, snapshot, portfolio, cfg, fixtures.NOW)
    }
