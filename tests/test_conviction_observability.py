from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from agent.config import AgentMode, Settings
from agent.db import build_engine, build_session_factory
from agent.decision import GrokDecisionProvider
from agent.market import PaperMarketData
from agent.repository import Repository
from agent.research import GrokXResearchProvider, NeutralResearchProvider
from agent.service import AgentService
from llm_checks import LLMLayerConfig
from llm_schemas import FeatureSheet


def _fake_grok_provider(recorder) -> GrokDecisionProvider:
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(parsed=None))],
        usage=SimpleNamespace(prompt_tokens=17, completion_tokens=3),
    )
    completions = SimpleNamespace(parse=lambda **_: response)
    provider = GrokDecisionProvider.__new__(GrokDecisionProvider)
    provider.client = SimpleNamespace(
        beta=SimpleNamespace(chat=SimpleNamespace(completions=completions))
    )
    provider.model = "grok-test"
    provider.config = LLMLayerConfig()
    provider.trading_profile = "conservative"
    provider.strategist_refresh_seconds = 1800.0
    provider.live_autonomous = False
    provider._playbook_cache = None
    provider.recorder = recorder
    provider.strategist_prompt = (
        "{{profile_directive}} {{min_plan_conviction}} {{ttl_min_hours}} {{ttl_max_hours}}"
    )
    provider.trader_prompt = "unused"
    provider.risk_review_prompt = "unused"
    return provider


def test_strategist_parse_failure_is_persisted_in_llm_calls() -> None:
    engine = build_engine("sqlite://")
    repository = Repository(engine, build_session_factory(engine))
    repository.initialize()
    provider = _fake_grok_provider(repository.record_llm_call)
    feature_sheet = PaperMarketData().snapshot()

    with pytest.raises(RuntimeError, match="no structured output"):
        provider.decide(
            feature_sheet,
            signals=[],
            research=NeutralResearchProvider().research(feature_sheet),
            positions=[],
            cycle_id="parse-failure-cycle",
        )

    call = repository.dashboard()["llm_calls"][0]
    assert call["cycle_id"] == "parse-failure-cycle"
    assert call["stage"] == "strategist"
    assert call["status"] == "FAILED"
    assert call["skipped_reason"] == "STRUCTURED_OUTPUT_PARSE_FAILED"
    assert call["response"]["error"] == {
        "code": "STRUCTURED_OUTPUT_PARSE_FAILED",
        "type": "RuntimeError",
        "message": "Grok strategist returned no structured output",
    }
    assert call["input_tokens"] == 17
    assert call["output_tokens"] == 3


def test_research_cache_hit_is_realigned_to_current_universe() -> None:
    provider = GrokXResearchProvider.__new__(GrokXResearchProvider)
    provider.model = "grok-test"
    provider.cache_seconds = 900.0
    provider.recorder = lambda _: None
    provider._cache = None
    provider.seed_cache(
        {
            "as_of": datetime.now(timezone.utc).isoformat(),
            "signals": [
                {
                    "symbol": symbol,
                    "direction": "LONG",
                    "confidence": 0.7,
                    "novelty": 0.5,
                    "manipulation_risk": 0.1,
                    "summary": f"Cached catalyst for {symbol}",
                    "sources": [f"https://example.test/{symbol.lower()}"],
                }
                for symbol in ("BTC", "ETH", "SOL")
            ],
        }
    )
    current = PaperMarketData().snapshot().model_dump(mode="json")
    current["assets"][0]["symbol"] = "XRP"
    feature_sheet = FeatureSheet.model_validate(current)

    result = provider.research(feature_sheet, cycle_id="cache-realignment")

    assert [item.symbol for item in result.signals] == ["XRP", "ETH", "SOL"]
    assert result.signals[0].confidence == 0.0
    assert result.signals[0].source_urls == []
    assert "No cached research" in result.signals[0].summary
    serialized = result.model_dump(mode="json")
    assert all("source_urls" in item for item in serialized["signals"])
    assert all("sources" not in item for item in serialized["signals"])
    # Projection must not destroy still-valid cached evidence for later universes.
    assert [item.symbol for item in provider._cache[1].signals] == ["BTC", "ETH", "SOL"]


class _FailingDecisionProvider:
    name = "controlled-failure"

    def decide(self, *args, **kwargs):
        raise ValueError("controlled strategist parse failure")


def test_decision_fallback_exposes_safe_hold_and_degraded_cycle() -> None:
    service = AgentService(
        Settings(
            _env_file=None,
            agent_mode=AgentMode.PAPER,
            database_url="sqlite://",
            llm_provider="rules",
        )
    )
    service.graph_dependencies.decisions = _FailingDecisionProvider()

    result = service.run_cycle()

    assert result["status"] == "DEGRADED"
    assert result["health_status"] == "DEGRADED"
    assert result["decision_status"] == "DEGRADED"
    assert result["decision_provenance"] == "SAFE_HOLD"
    assert result["decision"]["status"] == "DEGRADED"
    assert result["decision"]["provenance"] == "SAFE_HOLD"
    assert all(item["action"] == "HOLD" for item in result["decision"]["trader"]["decisions"])
    assert "DECISION_PROVIDER_FAILED" in {item["code"] for item in result["decision"]["reasons"]}
    assert result["decision"]["conviction_diagnostics"]
    assert service.dashboard()["cycles"][0]["status"] == "DEGRADED"
