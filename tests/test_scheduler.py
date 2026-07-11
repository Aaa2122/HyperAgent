from __future__ import annotations

import time

from agent.config import Settings
from agent.scheduler import AutomationScheduler


class FakeRepository:
    def __init__(self):
        self.events = []

    def add_event(self, event_type, payload, **kwargs):
        self.events.append((event_type, payload, kwargs))


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
    assert status["last_risk_monitor_status"] == "OK:0"
