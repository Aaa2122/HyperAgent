from __future__ import annotations

import hashlib
import uuid
from typing import Protocol

from agent.config import AgentMode
from agent.domain import ApprovedOrder, ExecutionResult, KillSwitchState
from agent.protection import build_protection_specs
from agent.repository import Repository

INTENT_NAMESPACE = uuid.UUID("ea82c391-79cc-4cb6-a6a8-c6e22689673a")


def build_intent_identity(decision_key: str) -> tuple[str, str]:
    intent_uuid = uuid.uuid5(INTENT_NAMESPACE, decision_key)
    cloid = "0x" + hashlib.sha256(intent_uuid.bytes).digest()[:16].hex()
    return str(intent_uuid), cloid


class ExecutionService(Protocol):
    def execute(self, order: ApprovedOrder) -> ExecutionResult: ...

    def reconcile(self) -> list[ExecutionResult]: ...

    def positions(self) -> list[dict]: ...

    def monitor(self, marks: dict[str, float]) -> list[dict]: ...


class PaperExecutionService:
    def __init__(self, repository: Repository, mode: AgentMode):
        self.repository = repository
        self.mode = mode

    def execute(self, order: ApprovedOrder) -> ExecutionResult:
        # I3 second door: direct repository read, immediately before the side effect.
        if self.repository.current_kill_switch() is not KillSwitchState.RUNNING:
            raise PermissionError("kill-switch changed before submission")

        intent_id, cloid = build_intent_identity(order.decision_key)
        initial_status = "SIMULATED" if self.mode is AgentMode.DRY_RUN else "PENDING"
        intent, created = self.repository.get_or_create_intent(
            intent_id=intent_id,
            cloid=cloid,
            order=order,
            status=initial_status,
        )
        if not created:
            return ExecutionResult(
                intent_id=intent["intent_id"],
                cloid=intent["cloid"],
                symbol=order.symbol,
                status=intent["status"],
                duplicate_prevented=True,
            )

        if self.mode is AgentMode.PAPER:
            if order.action == "OPEN":
                self.repository.open_paper_position(order)
                specs = build_protection_specs(order, cloid)
                self.repository.ensure_protective_orders(
                    intent_id, order.cycle_id, specs, "ACTIVE"
                )
            elif order.action == "REDUCE":
                current = next(
                    (p for p in self.repository.positions() if p["symbol"] == order.symbol),
                    None,
                )
                if current is not None:
                    fraction = min(1.0, order.notional_usd / current["notional_usd"])
                    self.repository.reduce_paper_position(order.symbol, fraction)
                    self.repository.cancel_protections(order.symbol)
                    remaining = next(
                        (
                            p
                            for p in self.repository.positions()
                            if p["symbol"] == order.symbol
                        ),
                        None,
                    )
                    if remaining is not None:
                        replacement = order.model_copy(
                            update={
                                "notional_usd": remaining["notional_usd"],
                                "invalidation_px": remaining["invalidation_px"],
                                "targets": remaining.get("targets", []),
                                "leverage": remaining.get("leverage", 1),
                            }
                        )
                        specs = build_protection_specs(replacement, cloid)
                        self.repository.ensure_protective_orders(
                            intent_id, order.cycle_id, specs, "ACTIVE"
                        )
            elif order.action == "CLOSE":
                self.repository.close_paper_position(order.symbol)
                self.repository.cancel_protections(order.symbol)
            self.repository.mark_intent(intent_id, "FILLED")
            status = "FILLED"
        else:
            status = "SIMULATED"
        return ExecutionResult(
            intent_id=intent_id,
            cloid=cloid,
            symbol=order.symbol,
            status=status,
        )

    def reconcile(self) -> list[ExecutionResult]:
        return []

    def positions(self) -> list[dict]:
        return self.repository.positions()

    def monitor(self, marks: dict[str, float]) -> list[dict]:
        if self.mode is not AgentMode.PAPER:
            return []
        active = self.repository.protective_orders(statuses={"ACTIVE"})
        protected_symbols = {item["symbol"] for item in active}
        for position in self.repository.positions():
            if position["symbol"] in protected_symbols:
                continue
            metadata = self.repository.latest_filled_open_intent(position["symbol"])
            if metadata is None:
                continue
            order = ApprovedOrder.model_validate(metadata["payload"])
            if not order.targets:
                plan = self.repository.latest_playbook_plan(position["symbol"])
                if plan is not None:
                    order = order.model_copy(
                        update={"targets": list(plan.get("targets", []))}
                    )
            specs = build_protection_specs(order, metadata["cloid"])
            self.repository.ensure_protective_orders(
                metadata["intent_id"], metadata["cycle_id"], specs, "ACTIVE"
            )
        return self.repository.apply_paper_protection_triggers(marks)
