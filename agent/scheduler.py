from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any

from agent.config import Settings
from agent.service import AgentService


class AutomationScheduler:
    def __init__(self, service: AgentService, settings: Settings):
        self.service = service
        self.settings = settings
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._state_lock = threading.Lock()
        self._state: dict[str, Any] = {
            "enabled": settings.automation_enabled,
            "running": False,
            "last_cycle_started_at": None,
            "last_cycle_finished_at": None,
            "last_cycle_status": None,
            "last_risk_monitor_at": None,
            "last_risk_monitor_status": None,
        }

    def start(self) -> None:
        with self._state_lock:
            enabled = bool(self._state["enabled"])
        if not enabled or self._threads:
            return
        self._stop.clear()
        self._threads = [
            threading.Thread(
                target=self._cycle_loop,
                name="agent-cycle-scheduler",
                daemon=True,
            ),
            threading.Thread(
                target=self._risk_loop,
                name="agent-risk-monitor",
                daemon=True,
            ),
        ]
        with self._state_lock:
            self._state["running"] = True
        for thread in self._threads:
            thread.start()

    def stop(self) -> None:
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=5)
        self._threads = []
        with self._state_lock:
            self._state["running"] = False

    def status(self) -> dict[str, Any]:
        with self._state_lock:
            return {
                **self._state,
                "cycle_interval_seconds": self.settings.cycle_interval_seconds,
                "risk_monitor_interval_seconds": (
                    self.settings.risk_monitor_interval_seconds
                ),
                "x_research_cache_seconds": self.settings.x_research_cache_seconds,
                "strategist_refresh_seconds": (
                    self.settings.strategist_refresh_seconds
                ),
            }

    def set_enabled(self, enabled: bool) -> dict[str, Any]:
        with self._state_lock:
            self._state["enabled"] = enabled
        if enabled:
            self.start()
        else:
            self.stop()
        return self.status()

    def configure(
        self,
        *,
        enabled: bool | None = None,
        cycle_interval_seconds: float | None = None,
        risk_monitor_interval_seconds: float | None = None,
    ) -> dict[str, Any]:
        if cycle_interval_seconds is not None:
            self.settings.cycle_interval_seconds = cycle_interval_seconds
        if risk_monitor_interval_seconds is not None:
            self.settings.risk_monitor_interval_seconds = (
                risk_monitor_interval_seconds
            )
        if enabled is not None:
            return self.set_enabled(enabled)
        return self.status()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _cycle_loop(self) -> None:
        while not self._stop.is_set():
            with self._state_lock:
                self._state["last_cycle_started_at"] = self._now()
                self._state["last_cycle_status"] = "RUNNING"
            try:
                runner = getattr(self.service, "run_scheduled_cycle", self.service.run_cycle)
                result = runner()
                status = result.get("status", "COMPLETED")
            except Exception as exc:
                status = f"FAILED:{type(exc).__name__}"
                self.service.repository.add_event(
                    "AUTOMATED_CYCLE_FAILED",
                    {"error": type(exc).__name__},
                    severity="ERROR",
                )
            with self._state_lock:
                self._state["last_cycle_finished_at"] = self._now()
                self._state["last_cycle_status"] = status
            if self._stop.wait(self.settings.cycle_interval_seconds):
                break

    def _risk_loop(self) -> None:
        while not self._stop.is_set():
            try:
                changes = self.service.monitor_risk()
                status = f"OK:{len(changes)}"
            except Exception as exc:
                status = f"FAILED:{type(exc).__name__}"
                self.service.repository.add_event(
                    "RISK_MONITOR_FAILED",
                    {"error": type(exc).__name__},
                    severity="ERROR",
                )
            with self._state_lock:
                self._state["last_risk_monitor_at"] = self._now()
                self._state["last_risk_monitor_status"] = status
            if self._stop.wait(self.settings.risk_monitor_interval_seconds):
                break
