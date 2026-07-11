from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from agent.config import Settings
from agent.service import AgentService


class AutomationScheduler:
    def __init__(self, service: AgentService, settings: Settings):
        self.service = service
        self.settings = settings
        self._stop = threading.Event()
        self._schedule_changed = threading.Event()
        self._threads: list[threading.Thread] = []
        self._state_lock = threading.Lock()
        self._state: dict[str, Any] = {
            "enabled": settings.automation_enabled,
            "running": False,
            "last_cycle_started_at": None,
            "last_cycle_finished_at": None,
            "last_cycle_status": None,
            "last_cycle_duration_seconds": None,
            "last_cycle_reason": None,
            "next_cycle_at": None,
            "last_risk_monitor_at": None,
            "last_risk_monitor_status": None,
        }

    def start(self) -> None:
        with self._state_lock:
            enabled = bool(self._state["enabled"])
        if not enabled or self._threads:
            return
        self._stop.clear()
        self._schedule_changed.clear()
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
            self._state["next_cycle_at"] = self._now()
        for thread in self._threads:
            thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._schedule_changed.set()
        for thread in self._threads:
            thread.join(timeout=5)
        self._threads = []
        with self._state_lock:
            self._state["running"] = False
            self._state["next_cycle_at"] = None

    def status(self) -> dict[str, Any]:
        activity_getter = getattr(self.service, "activity_status", None)
        activity = activity_getter() if activity_getter else {
            "phase": "RUNNING" if self._state.get("running") else "WAITING",
            "phase_started_at": None,
            "phase_detail": None,
        }
        with self._state_lock:
            return {
                **self._state,
                **activity,
                "server_time": self._now(),
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
        self.settings.automation_enabled = enabled
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
            with self._state_lock:
                if self._state["running"] and self._state["last_cycle_status"] != "RUNNING":
                    finished = self._state["last_cycle_finished_at"]
                    base = (
                        datetime.fromisoformat(finished)
                        if finished
                        else datetime.now(timezone.utc)
                    )
                    target = base + timedelta(seconds=cycle_interval_seconds)
                    self._state["next_cycle_at"] = max(
                        target, datetime.now(timezone.utc)
                    ).isoformat()
            self._schedule_changed.set()
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
            started_mono = time.monotonic()
            with self._state_lock:
                self._state["last_cycle_started_at"] = self._now()
                self._state["last_cycle_status"] = "RUNNING"
                self._state["last_cycle_reason"] = None
                self._state["next_cycle_at"] = None
            try:
                runner = getattr(self.service, "run_scheduled_cycle", self.service.run_cycle)
                result = runner()
                status = result.get("status", "COMPLETED")
                policy = result.get("policy") or {}
                reason = policy.get("event_reason") or policy.get("reason")
            except Exception as exc:
                status = f"FAILED:{type(exc).__name__}"
                reason = str(exc)[:180]
                self.service.repository.add_event(
                    "AUTOMATED_CYCLE_FAILED",
                    {"error": type(exc).__name__},
                    severity="ERROR",
                )
            with self._state_lock:
                self._state["last_cycle_finished_at"] = self._now()
                self._state["last_cycle_status"] = status
                self._state["last_cycle_duration_seconds"] = round(
                    time.monotonic() - started_mono, 3
                )
                self._state["last_cycle_reason"] = reason
                self._state["next_cycle_at"] = (
                    datetime.now(timezone.utc)
                    + timedelta(seconds=self.settings.cycle_interval_seconds)
                ).isoformat()
            if self._wait_for_next_cycle():
                break

    def _wait_for_next_cycle(self) -> bool:
        """Wait until the authoritative deadline, reacting to cadence changes."""
        while not self._stop.is_set():
            self._schedule_changed.clear()
            with self._state_lock:
                next_cycle_at = self._state["next_cycle_at"]
            if not next_cycle_at:
                return self._stop.is_set()
            deadline = datetime.fromisoformat(next_cycle_at)
            delay = max(
                0.0,
                (deadline - datetime.now(timezone.utc)).total_seconds(),
            )
            if delay <= 0:
                return False
            changed = self._schedule_changed.wait(delay)
            if self._stop.is_set():
                return True
            if not changed:
                return False
        return True

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
