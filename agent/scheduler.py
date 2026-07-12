from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from agent.activation import (
    ActivationConfig,
    ActivationMode,
    CryptoSession,
    UsEquitySession,
)
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
            "risk_monitor_running": False,
            "activation_state": (
                "WAITING" if settings.automation_enabled else "BLOCKED"
            ),
            "activation_reason": (
                "INITIALIZING"
                if settings.automation_enabled
                else "AUTOMATION_DISABLED"
            ),
            "next_activation_window_at": None,
            "next_activation_window_local": None,
            "cycle_policy": None,
        }

    def start(self) -> None:
        with self._state_lock:
            enabled = bool(self._state["enabled"])
        if self._threads:
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
            self._state["running"] = enabled
            self._state["risk_monitor_running"] = True
            self._state["next_cycle_at"] = self._now() if enabled else None
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
            self._state["risk_monitor_running"] = False
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
                "activation_config": self._activation_config().public_dict(),
            }

    def set_enabled(self, enabled: bool) -> dict[str, Any]:
        self.settings.automation_enabled = enabled
        with self._state_lock:
            self._state["enabled"] = enabled
            self._state["running"] = enabled and bool(self._threads)
            self._state["next_cycle_at"] = self._now() if enabled else None
            self._state["activation_state"] = (
                "WAITING" if enabled else "BLOCKED"
            )
            self._state["activation_reason"] = (
                "AUTOMATION_ENABLED" if enabled else "AUTOMATION_DISABLED"
            )
        if not self._threads:
            self.start()
        self._schedule_changed.set()
        return self.status()

    def _activation_config(self) -> ActivationConfig:
        return ActivationConfig(
            mode=self.settings.activation_mode,
            timezone=self.settings.activation_timezone,
            us_equities_sessions=self.settings.us_equities_sessions,
            crypto_sessions=self.settings.crypto_sessions,
            liquidity_filter_enabled=self.settings.liquidity_filter_enabled,
            liquidity_min_24h_volume_usd=(
                self.settings.liquidity_min_24h_volume_usd
            ),
            liquidity_min_open_interest_usd=(
                self.settings.liquidity_min_open_interest_usd
            ),
            liquidity_min_eligible_assets=(
                self.settings.liquidity_min_eligible_assets
            ),
        )

    def configure(
        self,
        *,
        enabled: bool | None = None,
        cycle_interval_seconds: float | None = None,
        risk_monitor_interval_seconds: float | None = None,
        activation_mode: ActivationMode | None = None,
        activation_timezone: str | None = None,
        us_equities_sessions: list[UsEquitySession] | None = None,
        crypto_sessions: list[CryptoSession] | None = None,
        liquidity_filter_enabled: bool | None = None,
        liquidity_min_24h_volume_usd: float | None = None,
        liquidity_min_open_interest_usd: float | None = None,
        liquidity_min_eligible_assets: int | None = None,
    ) -> dict[str, Any]:
        activation_changed = any(
            item is not None
            for item in (
                activation_mode,
                activation_timezone,
                us_equities_sessions,
                crypto_sessions,
                liquidity_filter_enabled,
                liquidity_min_24h_volume_usd,
                liquidity_min_open_interest_usd,
                liquidity_min_eligible_assets,
            )
        )
        if activation_changed:
            # Validate the complete effective configuration before mutating a
            # single runtime setting. This makes updates atomic.
            candidate = ActivationConfig(
                mode=(
                    activation_mode
                    if activation_mode is not None
                    else self.settings.activation_mode
                ),
                timezone=(
                    activation_timezone
                    if activation_timezone is not None
                    else self.settings.activation_timezone
                ),
                us_equities_sessions=(
                    us_equities_sessions
                    if us_equities_sessions is not None
                    else self.settings.us_equities_sessions
                ),
                crypto_sessions=(
                    crypto_sessions
                    if crypto_sessions is not None
                    else self.settings.crypto_sessions
                ),
                liquidity_filter_enabled=(
                    liquidity_filter_enabled
                    if liquidity_filter_enabled is not None
                    else self.settings.liquidity_filter_enabled
                ),
                liquidity_min_24h_volume_usd=(
                    liquidity_min_24h_volume_usd
                    if liquidity_min_24h_volume_usd is not None
                    else self.settings.liquidity_min_24h_volume_usd
                ),
                liquidity_min_open_interest_usd=(
                    liquidity_min_open_interest_usd
                    if liquidity_min_open_interest_usd is not None
                    else self.settings.liquidity_min_open_interest_usd
                ),
                liquidity_min_eligible_assets=(
                    liquidity_min_eligible_assets
                    if liquidity_min_eligible_assets is not None
                    else self.settings.liquidity_min_eligible_assets
                ),
            )
            self.settings.activation_mode = candidate.mode
            self.settings.activation_timezone = candidate.timezone
            self.settings.us_equities_sessions = list(
                candidate.us_equities_sessions
            )
            self.settings.crypto_sessions = list(candidate.crypto_sessions)
            self.settings.liquidity_filter_enabled = (
                candidate.liquidity_filter_enabled
            )
            self.settings.liquidity_min_24h_volume_usd = (
                candidate.liquidity_min_24h_volume_usd
            )
            self.settings.liquidity_min_open_interest_usd = (
                candidate.liquidity_min_open_interest_usd
            )
            self.settings.liquidity_min_eligible_assets = (
                candidate.liquidity_min_eligible_assets
            )
            with self._state_lock:
                if self._state["enabled"]:
                    self._state["next_cycle_at"] = self._now()
                    self._state["activation_state"] = "WAITING"
                    self._state["activation_reason"] = "CONFIGURATION_UPDATED"
            self._schedule_changed.set()
        if cycle_interval_seconds is not None:
            self.settings.cycle_interval_seconds = cycle_interval_seconds
            with self._state_lock:
                if self._state["running"] and self._state["last_cycle_status"] != "RUNNING":
                    if (
                        self._state["activation_reason"]
                        == "OUTSIDE_ACTIVATION_WINDOW"
                        and self._state["next_activation_window_at"]
                    ):
                        # Cadence changes must not pull an inactive market
                        # window forward and create repetitive skipped cycles.
                        self._state["next_cycle_at"] = self._state[
                            "next_activation_window_at"
                        ]
                    else:
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
            with self._state_lock:
                enabled = bool(self._state["enabled"])
            if not enabled:
                self._schedule_changed.wait(0.5)
                self._schedule_changed.clear()
                continue
            started_mono = time.monotonic()
            with self._state_lock:
                self._state["last_cycle_started_at"] = self._now()
                self._state["last_cycle_status"] = "RUNNING"
                self._state["last_cycle_reason"] = None
                self._state["next_cycle_at"] = None
            policy: dict[str, Any] = {}
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
                enabled = bool(self._state["enabled"])
                self._state["last_cycle_finished_at"] = self._now()
                self._state["last_cycle_status"] = status
                self._state["last_cycle_duration_seconds"] = round(
                    time.monotonic() - started_mono, 3
                )
                self._state["last_cycle_reason"] = reason
                self._state["cycle_policy"] = policy or None
                self._state["activation_state"] = policy.get(
                    "state", "BLOCKED" if status.startswith("FAILED") else "ACTIVE"
                )
                self._state["activation_reason"] = policy.get(
                    "reason", "CYCLE_FAILED" if status.startswith("FAILED") else None
                )
                self._state["next_activation_window_at"] = policy.get(
                    "next_window_at"
                )
                self._state["next_activation_window_local"] = policy.get(
                    "next_window_local"
                )
                if not enabled:
                    self._state["next_cycle_at"] = None
                elif (
                    policy.get("reason") == "OUTSIDE_ACTIVATION_WINDOW"
                    and policy.get("next_window_at")
                ):
                    self._state["next_cycle_at"] = policy["next_window_at"]
                else:
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
        # Stagger the independent monitor from scheduler/bootstrap database
        # writes. It still runs at the configured cadence even when LLM
        # automation is disabled or waiting for a market window.
        if self._stop.wait(self.settings.risk_monitor_interval_seconds):
            return
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
