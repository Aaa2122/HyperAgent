from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from time import monotonic

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from agent.activation import (
    ActivationConfig,
    ActivationMode,
    CryptoSession,
    LiquidityObservation,
    UsEquitySession,
    evaluate_activation,
    evaluate_session_window,
)
from agent.api import create_app
from agent.config import AgentMode, Settings
from agent.hyperliquid import HyperliquidMarketData
from agent.scheduler import AutomationScheduler
from agent.service import AgentService


def _wait_until(predicate, *, timeout: float = 1.0) -> None:
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    pytest.fail("scheduler condition was not reached before timeout")


def paper_settings(**overrides) -> Settings:
    return Settings(
        _env_file=None,
        agent_mode=AgentMode.PAPER,
        database_url="sqlite://",
        llm_provider="rules",
        **overrides,
    )


def test_default_is_strictly_always_with_filter_disabled() -> None:
    settings = paper_settings()
    config = ActivationConfig(
        mode=settings.activation_mode,
        timezone=settings.activation_timezone,
        us_equities_sessions=settings.us_equities_sessions,
        crypto_sessions=settings.crypto_sessions,
        liquidity_filter_enabled=settings.liquidity_filter_enabled,
        liquidity_min_24h_volume_usd=settings.liquidity_min_24h_volume_usd,
        liquidity_min_open_interest_usd=(settings.liquidity_min_open_interest_usd),
        liquidity_min_eligible_assets=settings.liquidity_min_eligible_assets,
    )

    assert config.mode is ActivationMode.ALWAYS
    assert config.liquidity_filter_enabled is False
    decision = evaluate_activation(
        config,
        at=datetime(2026, 7, 12, 3, 0, tzinfo=timezone.utc),
    )
    assert decision.state == "ACTIVE"
    assert decision.reason == "ALWAYS_ACTIVE"
    assert decision.liquidity["status"] == "DISABLED"


@pytest.mark.parametrize(
    ("at", "expected_end_hour_utc"),
    [
        (datetime(2026, 1, 12, 14, 35, tzinfo=timezone.utc), 17),
        (datetime(2026, 7, 13, 13, 35, tzinfo=timezone.utc), 16),
    ],
)
def test_us_equities_windows_follow_new_york_dst(
    at: datetime,
    expected_end_hour_utc: int,
) -> None:
    config = ActivationConfig(
        mode="us_equities",
        timezone="Europe/Paris",
        us_equities_sessions=["market_open", "first_hours"],
    )

    decision = evaluate_session_window(config, at=at)

    assert decision.state == "ACTIVE"
    assert "us_equities:market_open" in decision.active_sessions
    assert decision.active_window_ends_at is not None
    assert decision.active_window_ends_at.hour == expected_end_hour_utc
    assert decision.schedule_basis.startswith("America/New_York")


def test_us_equities_weekend_waits_until_monday_and_renders_local_time() -> None:
    config = ActivationConfig(
        mode="us_equities",
        timezone="Europe/Paris",
        us_equities_sessions=[UsEquitySession.MARKET_OPEN],
    )

    decision = evaluate_session_window(
        config,
        at=datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc),  # Sunday
    )

    assert decision.state == "WAITING"
    assert decision.reason == "OUTSIDE_ACTIVATION_WINDOW"
    assert decision.next_window_at == datetime(2026, 7, 13, 13, 30, tzinfo=timezone.utc)
    assert decision.next_window_local is not None
    assert decision.next_window_local.hour == 15


def test_crypto_selected_sessions_are_canonical_utc_windows() -> None:
    config = ActivationConfig(
        mode="crypto_sessions",
        timezone="Asia/Tokyo",
        crypto_sessions=[CryptoSession.EUROPE_US_OVERLAP],
    )

    open_decision = evaluate_session_window(
        config,
        at=datetime(2026, 7, 12, 14, 0, tzinfo=timezone.utc),
    )
    closed_decision = evaluate_session_window(
        config,
        at=datetime(2026, 7, 12, 18, 0, tzinfo=timezone.utc),
    )

    assert open_decision.state == "ACTIVE"
    assert open_decision.active_sessions == ["crypto:europe_us_overlap"]
    assert closed_decision.state == "WAITING"
    assert closed_decision.next_window_at == datetime(2026, 7, 13, 13, 0, tzinfo=timezone.utc)


def test_liquidity_filter_has_explicit_bootstrap_pass_and_wait_states() -> None:
    config = ActivationConfig(
        mode="always",
        liquidity_filter_enabled=True,
        liquidity_min_24h_volume_usd=100_000_000,
        liquidity_min_open_interest_usd=50_000_000,
        liquidity_min_eligible_assets=2,
    )
    now = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)

    bootstrap = evaluate_activation(config, at=now)
    assert bootstrap.state == "BLOCKED"
    assert bootstrap.reason == "LIQUIDITY_OBSERVATION_PENDING"
    assert bootstrap.liquidity["status"] == "PENDING_OBSERVATION"

    passed = evaluate_activation(
        config,
        at=now,
        observation={
            "as_of": now,
            "source": "fixture",
            "assets": [
                {
                    "symbol": "BTC",
                    "volume_24h_usd": 500_000_000,
                    "open_interest_usd": 400_000_000,
                },
                {
                    "symbol": "ETH",
                    "volume_24h_usd": 300_000_000,
                    "open_interest_usd": 200_000_000,
                },
            ],
        },
    )
    assert passed.state == "ACTIVE"
    assert passed.reason == "ACTIVATION_CONDITIONS_MET"
    assert passed.liquidity["eligible_assets"] == ["BTC", "ETH"]

    below = evaluate_activation(
        config,
        at=now,
        observation={
            "as_of": now,
            "source": "fixture",
            "assets": [
                {
                    "symbol": "BTC",
                    "volume_24h_usd": 500_000_000,
                    "open_interest_usd": 400_000_000,
                },
                {
                    "symbol": "ETH",
                    "volume_24h_usd": 2_000_000,
                    "open_interest_usd": 1_000_000,
                },
            ],
        },
    )
    assert below.state == "WAITING"
    assert below.reason == "LIQUIDITY_FILTER_NOT_MET"


def test_missing_required_liquidity_fields_is_blocked_not_treated_as_zero() -> None:
    config = ActivationConfig(
        mode="always",
        liquidity_filter_enabled=True,
    )
    decision = evaluate_activation(
        config,
        at=datetime(2026, 7, 12, tzinfo=timezone.utc),
        observation=LiquidityObservation(
            as_of=datetime(2026, 7, 12, tzinfo=timezone.utc),
            source="fixture",
            assets=[{"symbol": "BTC"}],
        ),
    )

    assert decision.state == "BLOCKED"
    assert decision.reason == "LIQUIDITY_DATA_UNAVAILABLE"


def test_hybrid_requires_sessions_and_deterministic_filter() -> None:
    with pytest.raises(ValidationError, match="requires the liquidity filter"):
        ActivationConfig(mode="hybrid")
    with pytest.raises(ValidationError, match="at least one selected session"):
        ActivationConfig(
            mode="hybrid",
            us_equities_sessions=[],
            crypto_sessions=[],
            liquidity_filter_enabled=True,
        )


def test_service_default_policy_does_not_probe_liquidity() -> None:
    service = AgentService(paper_settings())
    calls = 0

    def forbidden_probe():
        nonlocal calls
        calls += 1
        raise AssertionError("default always mode must not probe liquidity")

    service.market.activation_metrics = forbidden_probe
    policy = service.scheduled_cycle_policy(at=datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc))

    assert policy["run"] is True
    assert policy["state"] == "ACTIVE"
    assert policy["reason"] == "NON_LIVE_MODE"
    assert policy["activation"]["reason"] == "ALWAYS_ACTIVE"
    assert calls == 0


def test_service_probes_filter_once_without_running_graph_or_llm() -> None:
    service = AgentService(
        paper_settings(
            liquidity_filter_enabled=True,
            liquidity_min_eligible_assets=1,
        )
    )
    calls = 0
    original = service.market.activation_metrics

    def counted_probe():
        nonlocal calls
        calls += 1
        return original()

    service.market.activation_metrics = counted_probe
    now = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)

    first = service.scheduled_cycle_policy(at=now)
    second = service.scheduled_cycle_policy(at=now)

    assert first["run"] is True
    assert first["activation"]["liquidity"]["status"] == "PASSED"
    assert second["run"] is True
    assert calls == 1
    assert service.repository.dashboard()["cycles"] == []
    assert service.repository.dashboard()["llm_calls"] == []


def test_service_outside_window_skips_probe_and_reports_exact_next_window() -> None:
    service = AgentService(
        paper_settings(
            activation_mode="crypto_sessions",
            crypto_sessions=["europe_us_overlap"],
            liquidity_filter_enabled=True,
        )
    )
    calls = 0

    def forbidden_probe():
        nonlocal calls
        calls += 1
        return {}

    service.market.activation_metrics = forbidden_probe
    policy = service.scheduled_cycle_policy(at=datetime(2026, 7, 12, 18, 0, tzinfo=timezone.utc))

    assert policy["run"] is False
    assert policy["state"] == "WAITING"
    assert policy["reason"] == "OUTSIDE_ACTIVATION_WINDOW"
    assert policy["next_window_at"] == "2026-07-13T13:00:00Z"
    assert calls == 0


def test_hyperliquid_activation_metrics_use_only_read_only_meta_context() -> None:
    class FakeInfoClient:
        def __init__(self):
            self.calls = 0

        def meta_and_asset_contexts(self):
            self.calls += 1
            return (
                {"universe": [{"name": "BTC"}, {"name": "ETH"}]},
                [
                    {
                        "dayNtlVlm": "500000000",
                        "openInterest": "1000",
                        "markPx": "65000",
                    },
                    {
                        "dayNtlVlm": "300000000",
                        "openInterest": "2000",
                        "markPx": "3400",
                    },
                ],
            )

    client = FakeInfoClient()
    market = HyperliquidMarketData(client=client, network="mainnet")
    result = market.activation_metrics()

    assert client.calls == 1
    assert result["source"] == "hyperliquid_mainnet_metaAndAssetCtxs"
    assert result["assets"][0]["volume_24h_usd"] == 500_000_000
    assert result["assets"][0]["open_interest_usd"] == 65_000_000


class _FakeRepository:
    def __init__(self):
        self.events = []

    def add_event(self, event_type, payload, **kwargs):
        self.events.append((event_type, payload, kwargs))


class _FakeSchedulerService:
    def __init__(self):
        self.repository = _FakeRepository()
        self.cycles = 0
        self.monitors = 0

    def run_cycle(self):
        self.cycles += 1
        return {"status": "COMPLETED"}

    def monitor_risk(self):
        self.monitors += 1
        return []


class _WindowPolicyService(_FakeSchedulerService):
    def __init__(self, next_window_at: str):
        super().__init__()
        self.next_window_at = next_window_at

    def run_scheduled_cycle(self):
        self.cycles += 1
        return {
            "status": "SKIPPED",
            "policy": {
                "run": False,
                "state": "WAITING",
                "reason": "OUTSIDE_ACTIVATION_WINDOW",
                "next_window_at": self.next_window_at,
                "next_window_local": self.next_window_at,
            },
        }


def test_risk_monitor_continues_when_llm_automation_is_disabled() -> None:
    settings = paper_settings(automation_enabled=False)
    settings.risk_monitor_interval_seconds = 0.02
    service = _FakeSchedulerService()
    scheduler = AutomationScheduler(service, settings)

    scheduler.start()
    _wait_until(lambda: service.monitors >= 2)
    running_status = scheduler.status()
    scheduler.stop()

    assert service.cycles == 0
    assert service.monitors >= 2
    assert running_status["running"] is False
    assert running_status["risk_monitor_running"] is True
    assert running_status["activation_reason"] == "AUTOMATION_DISABLED"
    assert scheduler.status()["risk_monitor_running"] is False


def test_scheduler_sleeps_until_exact_future_activation_window() -> None:
    settings = paper_settings(automation_enabled=True)
    settings.cycle_interval_seconds = 0.01
    settings.risk_monitor_interval_seconds = 0.02
    exact_window = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    service = _WindowPolicyService(exact_window)
    scheduler = AutomationScheduler(service, settings)

    scheduler.start()
    _wait_until(lambda: service.cycles >= 1 and service.monitors >= 2)
    status = scheduler.status()
    reconfigured = scheduler.configure(cycle_interval_seconds=0.03)
    scheduler.stop()

    assert service.cycles == 1
    assert service.monitors >= 2
    assert status["next_cycle_at"] == exact_window
    assert status["next_activation_window_at"] == exact_window
    assert status["activation_state"] == "WAITING"
    assert status["activation_reason"] == "OUTSIDE_ACTIVATION_WINDOW"
    assert reconfigured["next_cycle_at"] == exact_window


def test_scheduler_runtime_activation_update_is_atomic_and_visible() -> None:
    settings = paper_settings(automation_enabled=False)
    service = _FakeSchedulerService()
    scheduler = AutomationScheduler(service, settings)

    status = scheduler.configure(
        activation_mode="hybrid",
        activation_timezone="Europe/Paris",
        us_equities_sessions=[],
        crypto_sessions=["europe_us_overlap"],
        liquidity_filter_enabled=True,
        liquidity_min_24h_volume_usd=42_000_000,
        liquidity_min_open_interest_usd=11_000_000,
        liquidity_min_eligible_assets=2,
    )

    assert status["activation_config"] == {
        "mode": "hybrid",
        "timezone": "Europe/Paris",
        "us_equities_sessions": [],
        "crypto_sessions": ["europe_us_overlap"],
        "liquidity_filter": {
            "enabled": True,
            "min_24h_volume_usd": 42_000_000,
            "min_open_interest_usd": 11_000_000,
            "min_eligible_assets": 2,
        },
    }
    with pytest.raises(ValidationError):
        scheduler.configure(liquidity_filter_enabled=False)
    assert scheduler.status()["activation_config"] == status["activation_config"]


def test_automation_api_exposes_and_validates_runtime_activation_contract() -> None:
    with TestClient(create_app(paper_settings())) as client:
        updated = client.post(
            "/api/automation",
            json={
                "activation_mode": "crypto_sessions",
                "activation_timezone": "Europe/Paris",
                "crypto_sessions": ["europe_us_overlap"],
                "liquidity_filter_enabled": True,
                "liquidity_min_24h_volume_usd": 50_000_000,
            },
        )
        assert updated.status_code == 200
        payload = updated.json()
        assert payload["activation_config"]["mode"] == "crypto_sessions"
        assert payload["activation_config"]["timezone"] == "Europe/Paris"
        assert payload["activation_config"]["liquidity_filter"]["enabled"] is True

        status = client.get("/api/automation/status")
        assert status.status_code == 200
        assert status.json()["activation_config"] == payload["activation_config"]
        assert status.json()["activation_state"] in {
            "ACTIVE",
            "WAITING",
            "BLOCKED",
        }

        invalid = client.post(
            "/api/automation",
            json={"activation_timezone": "Mars/Olympus_Mons"},
        )
        assert invalid.status_code == 422
