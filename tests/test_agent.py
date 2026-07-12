from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from agent.api import create_app
from agent.config import AgentMode, Settings
from agent.decision import GrokDecisionProvider
from agent.domain import ApprovedOrder, KillSwitchState
from agent.execution import PaperExecutionService
from agent.market import PaperMarketData
from agent.service import AgentService


def settings(**overrides) -> Settings:
    values = {
        "agent_mode": AgentMode.PAPER,
        "database_url": "sqlite://",
        "llm_provider": "rules",
        **overrides,
    }
    return Settings(_env_file=None, **values)


def test_live_requires_one_time_gates_mainnet_credentials_and_postgres() -> None:
    with pytest.raises(ValidationError, match="LIVE_CONFIRMATION"):
        Settings(_env_file=None, agent_mode="live", database_url="sqlite://")
    configured = Settings(
        _env_file=None,
        agent_mode="live",
        automation_enabled=False,
        database_url="postgresql+psycopg://example.invalid/agent",
        live_confirmation="I_UNDERSTAND_THE_RISKS",
        hyperliquid_execution_network="mainnet",
        hyperliquid_account_address="0x" + "2" * 40,
        hyperliquid_private_key="0x" + "1" * 64,
        guardrails_configured=True,
    )
    assert configured.agent_mode is AgentMode.LIVE
    with pytest.raises(ValidationError, match="LIVE_AUTOMATION_CONFIRMATION"):
        Settings(
            _env_file=None,
            agent_mode="live",
            automation_enabled=True,
            database_url="postgresql+psycopg://example.invalid/agent",
            live_confirmation="I_UNDERSTAND_THE_RISKS",
            hyperliquid_execution_network="mainnet",
            hyperliquid_account_address="0x" + "2" * 40,
            hyperliquid_private_key="0x" + "1" * 64,
        )


def test_paper_cycle_is_end_to_end_and_does_not_reopen_positions() -> None:
    service = AgentService(settings())
    first = service.run_cycle()
    assert first["status"] == "COMPLETED"
    assert service.activity_status()["phase"] == "WAITING"
    assert first["market_snapshot"]["assets"]
    assert len(first["executions"]) >= 1
    assert all(item["status"] == "FILLED" for item in first["executions"])

    intent_count = len(service.dashboard()["intents"])
    second = service.run_cycle()
    assert second["status"] == "COMPLETED"
    assert second.get("executions", []) == []
    assert len(service.dashboard()["intents"]) == intent_count


def test_kill_switch_stops_cycle_before_market_data() -> None:
    service = AgentService(settings())
    service.repository.transition_kill_switch(
        KillSwitchState.PAUSED, "Operator pause for test", "pytest"
    )
    result = service.run_cycle()
    assert result["status"] == "SKIPPED"
    assert "market_snapshot" not in result
    assert service.dashboard()["intents"] == []


def test_scheduled_cycle_explains_operator_pause_without_starting_graph() -> None:
    service = AgentService(settings())
    service.repository.transition_kill_switch(
        KillSwitchState.PAUSED, "Operator pause for scheduler test", "pytest"
    )

    result = service.run_scheduled_cycle()

    assert result["status"] == "SKIPPED"
    assert result["policy"]["reason"] == "KILL_SWITCH_PAUSED"
    assert service.activity_status()["phase"] == "WAITING"
    assert service.activity_status()["phase_detail"] == "KILL_SWITCH_PAUSED"


def test_scheduled_cycle_uses_event_reason_when_market_did_not_change() -> None:
    service = AgentService(settings())
    first = service.run_scheduled_cycle()
    assert first["status"] == "COMPLETED"

    second = service.run_scheduled_cycle()

    assert second["status"] == "SKIPPED"
    assert second["policy"]["event_reason"] == "NO_MATERIAL_CHANGE"
    latest_call = service.dashboard()["llm_calls"][0]
    assert latest_call["stage"] == "cycle_policy"
    assert latest_call["skipped_reason"] == "NO_MATERIAL_CHANGE"


def test_decision_key_is_at_most_once() -> None:
    service = AgentService(settings())
    cycle_id = "00000000-0000-0000-0000-000000000001"
    service.repository.create_cycle(cycle_id, "paper", datetime.now(timezone.utc))
    executor = PaperExecutionService(service.repository, AgentMode.PAPER)
    order = ApprovedOrder(
        cycle_id=cycle_id,
        playbook_id="pb-test",
        symbol="BTC",
        action="OPEN",
        direction="LONG",
        notional_usd=500,
        mark_px=65_000,
        invalidation_px=63_500,
        targets=[67_000, 68_000],
        place_stop_order=True,
        take_profit_fractions=[0.6, 0.4],
        leverage=4,
        decision_key="a" * 64,
    )
    first = executor.execute(order)
    second = executor.execute(order)
    assert first.intent_id == second.intent_id
    assert first.cloid == second.cloid
    assert second.duplicate_prevented is True
    assert len(service.dashboard()["intents"]) == 1
    position = service.dashboard()["positions"][0]
    assert position["leverage"] == 4
    assert position["margin_used_usd"] == 125
    protections = service.repository.protective_orders(symbol="BTC")
    assert len(protections) == 3
    assert all(item["status"] == "ACTIVE" for item in protections)

    tp_changes = executor.monitor({"BTC": 67_000})
    assert tp_changes[0]["kind"] == "TP"
    reduced = service.dashboard()["positions"][0]
    assert reduced["notional_usd"] == 200

    stop_changes = executor.monitor({"BTC": 63_000})
    assert stop_changes[0]["kind"] == "SL"
    assert service.dashboard()["positions"] == []


def test_experimental_profile_is_visible_on_dashboard() -> None:
    service = AgentService(
        settings(trading_profile="experimental", max_model_leverage=5)
    )
    dashboard = service.dashboard()
    assert dashboard["trading_profile"] == "experimental"
    assert dashboard["max_model_leverage"] == 5


def test_llm_position_projection_includes_live_pnl_and_exposure() -> None:
    sheet = PaperMarketData().snapshot()
    positions = [
        {
            "symbol": "BTC",
            "side": "LONG",
            "entry_px": 64_000,
            "invalidation_px": 62_500,
            "notional_usd": 2_000,
            "leverage": 4,
            "equity_usd": 10_000,
            "unrealized_pnl_usd": 300,
            "roe_pct": 15,
        }
    ]
    projected = GrokDecisionProvider._prompt_positions(sheet, positions)
    payload = projected[0].model_dump()
    assert "unrealized_r" in payload
    assert payload["notional_usd"] == 2_000
    assert payload["unrealized_pnl_usd"] == 300
    assert payload["roe_pct"] == 15
    assert "equity_usd" not in payload


def test_startup_recovers_cycles_interrupted_by_a_previous_process(tmp_path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'recovery.db').as_posix()}"
    first = AgentService(settings(database_url=database_url))
    cycle_id = "00000000-0000-0000-0000-000000000999"
    first.repository.create_cycle(
        cycle_id,
        "paper",
        datetime.now(timezone.utc),
    )

    recovered = AgentService(settings(database_url=database_url))
    cycle = recovered.dashboard()["cycles"][0]

    assert cycle["cycle_id"] == cycle_id
    assert cycle["status"] == "FAILED"
    assert cycle["finished_at"] is not None
    assert cycle["error"].startswith("PROCESS_INTERRUPTED")
    assert cycle["state"]["incidents"][-1]["type"] == "PROCESS_INTERRUPTED"
    assert recovered.dashboard()["events"][0]["event_type"] == (
        "INTERRUPTED_CYCLES_RECOVERED"
    )


def test_api_cycle_and_dashboard() -> None:
    with TestClient(create_app(settings())) as client:
        assert client.get("/api/health").json()["mode"] == "paper"
        cycle = client.post("/api/cycles/run")
        assert cycle.status_code == 200
        dashboard = client.get("/api/dashboard").json()
        assert dashboard["cycles"][0]["status"] == "COMPLETED"
        assert dashboard["mode"] == "paper"
        assert dashboard["automation"]["phase"] == "WAITING"
        assert dashboard["automation"]["server_time"]
        runtime = client.get("/api/automation/status")
        assert runtime.status_code == 200
        assert runtime.json()["phase"] == "WAITING"


def test_halted_cannot_be_resumed_from_dashboard() -> None:
    with TestClient(create_app(settings())) as client:
        halted = client.post(
            "/api/killswitch",
            json={"state": "HALTED", "reason": "Emergency halt test", "actor": "pytest"},
        )
        assert halted.status_code == 200
        resumed = client.post(
            "/api/killswitch",
            json={"state": "RUNNING", "reason": "Unsafe direct resume", "actor": "pytest"},
        )
        assert resumed.status_code == 409


def test_dashboard_resume_wakes_scheduler_immediately() -> None:
    configured = settings().model_copy(update={"automation_enabled": True})
    with TestClient(create_app(configured)) as client:
        paused = client.post(
            "/api/killswitch",
            json={"state": "PAUSED", "reason": "Pause wake test", "actor": "pytest"},
        )
        assert paused.status_code == 200
        assert paused.json()["automation"]["next_cycle_at"] is None

        resumed = client.post(
            "/api/killswitch",
            json={"state": "RUNNING", "reason": "Resume wake test", "actor": "pytest"},
        )
        assert resumed.status_code == 200
        runtime = resumed.json()["automation"]
        assert runtime["activation_reason"] == "KILL_SWITCH_RESUMED"
        assert runtime["next_cycle_at"] is not None
