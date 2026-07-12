from __future__ import annotations

import time
from types import SimpleNamespace

from agent.config import Settings
from agent.scheduler import AutomationScheduler


class FakeRepository:
    def __init__(self):
        self.events = []

    def add_event(self, event_type, payload, **kwargs):
        self.events.append((event_type, payload, kwargs))

    def current_kill_switch(self):
        return SimpleNamespace(value="RUNNING")


class FakeService:
    def __init__(self):
        self.repository = FakeRepository()
        self.cycles = 0
        self.monitors = 0

    def run_cycle(self):
        self.cycles += 1
        return {"status": "COMPLETED"}

    def monitor_risk(self):
        self.monitors += 1
        return []


def test_automation_scheduler_runs_cycles_and_risk_monitor() -> None:
    settings = Settings(
        _env_file=None,
        automation_enabled=True,
        llm_provider="rules",
    )
    # Tests use short intervals; production validation still requires >=60s / >=5s.
    settings.cycle_interval_seconds = 0.04
    settings.risk_monitor_interval_seconds = 0.02
    service = FakeService()
    scheduler = AutomationScheduler(service, settings)

    scheduler.start()
    time.sleep(0.12)
    scheduler.stop()

    assert service.cycles >= 2
    assert service.monitors >= 3
    status = scheduler.status()
    assert status["running"] is False
    assert status["last_cycle_status"] == "COMPLETED"
    assert status["last_cycle_duration_seconds"] is not None
    assert status["last_cycle_duration_seconds"] >= 0
    assert status["next_cycle_at"] is None
    assert status["server_time"]
    assert status["phase"] in {"WAITING", "RUNNING"}
    assert status["last_risk_monitor_status"] == "OK:0"


def test_interval_change_recomputes_deadline_and_wakes_waiter() -> None:
    settings = Settings(
        _env_file=None,
        automation_enabled=True,
        llm_provider="rules",
    )
    settings.cycle_interval_seconds = 0.5
    settings.risk_monitor_interval_seconds = 0.2
    service = FakeService()
    scheduler = AutomationScheduler(service, settings)

    scheduler.start()
    time.sleep(0.08)
    assert service.cycles == 1
    previous_deadline = scheduler.status()["next_cycle_at"]

    scheduler.configure(cycle_interval_seconds=0.02)
    time.sleep(0.07)
    scheduler.stop()

    assert service.cycles >= 2
    assert scheduler.status()["next_cycle_at"] is None
    assert previous_deadline is not None


def test_kill_switch_resume_requeues_immediately() -> None:
    settings = Settings(_env_file=None, automation_enabled=True, llm_provider="rules")
    service = FakeService()
    scheduler = AutomationScheduler(service, settings)
    scheduler.start()
    time.sleep(0.03)

    paused = scheduler.on_kill_switch_changed("PAUSED")
    assert paused["next_cycle_at"] is None
    assert paused["activation_reason"] == "KILL_SWITCH_PAUSED"

    resumed = scheduler.on_kill_switch_changed("RUNNING")
    assert resumed["next_cycle_at"] is not None
    assert resumed["activation_reason"] == "KILL_SWITCH_RESUMED"
    scheduler.stop()
