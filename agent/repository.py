from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError

from agent.db import (
    AppEventRow,
    Base,
    CycleRow,
    KillSwitchEventRow,
    LlmCallRow,
    OrderIntentRow,
    PaperPositionRow,
    ProtectiveOrderRow,
)
from agent.domain import ApprovedOrder, KillSwitchState


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


class Repository:
    def __init__(self, engine, session_factory):
        self.engine = engine
        self.sessions = session_factory

    def initialize(self) -> None:
        Base.metadata.create_all(self.engine)
        with self.sessions.begin() as session:
            latest = session.scalar(
                select(KillSwitchEventRow).order_by(desc(KillSwitchEventRow.event_id)).limit(1)
            )
            if latest is None:
                session.add(
                    KillSwitchEventRow(
                        state=KillSwitchState.RUNNING.value,
                        reason="Initial safe state",
                        actor="system",
                    )
                )

    def current_kill_switch(self) -> KillSwitchState:
        with self.sessions() as session:
            row = session.scalar(
                select(KillSwitchEventRow).order_by(desc(KillSwitchEventRow.event_id)).limit(1)
            )
            if row is None:
                raise RuntimeError("kill-switch has not been initialized")
            return KillSwitchState(row.state)

    def recover_interrupted_cycles(self) -> list[str]:
        """Close cycles left RUNNING by a terminated process.

        A fresh service process cannot own work created by a previous process,
        so every RUNNING row found during startup is necessarily interrupted.
        Keeping it RUNNING would make the dashboard and automation telemetry
        claim work is still executing forever.
        """

        recovered: list[str] = []
        finished_at = datetime.now(timezone.utc)
        with self.sessions.begin() as session:
            rows = session.scalars(
                select(CycleRow).where(CycleRow.status == "RUNNING")
            ).all()
            for row in rows:
                recovered.append(row.cycle_id)
                state = dict(row.state or {})
                incidents = list(state.get("incidents") or [])
                incidents.append(
                    {
                        "type": "PROCESS_INTERRUPTED",
                        "recovered_at": finished_at.isoformat(),
                    }
                )
                state.update({"status": "FAILED", "incidents": incidents})
                row.status = "FAILED"
                row.finished_at = finished_at
                row.error = "PROCESS_INTERRUPTED: recovered during service startup"
                row.state = state
        return recovered

    def transition_kill_switch(
        self, state: KillSwitchState, reason: str, actor: str
    ) -> dict[str, Any]:
        with self.sessions.begin() as session:
            row = KillSwitchEventRow(state=state.value, reason=reason, actor=actor)
            session.add(row)
        return {
            "event_id": row.event_id,
            "state": row.state,
            "reason": row.reason,
            "actor": row.actor,
            "created_at": _iso(row.created_at),
        }

    def create_cycle(self, cycle_id: str, mode: str, started_at: datetime) -> None:
        with self.sessions.begin() as session:
            session.add(
                CycleRow(
                    cycle_id=cycle_id,
                    mode=mode,
                    status="RUNNING",
                    started_at=started_at,
                    state={},
                )
            )

    def complete_cycle(
        self,
        cycle_id: str,
        status: str,
        state: dict[str, Any],
        error: str | None = None,
    ) -> None:
        with self.sessions.begin() as session:
            row = session.get(CycleRow, cycle_id)
            if row is None:
                raise KeyError(cycle_id)
            row.status = status
            row.finished_at = datetime.now(timezone.utc)
            row.state = state
            row.error = error

    def add_event(
        self,
        event_type: str,
        payload: dict[str, Any],
        cycle_id: str | None = None,
        severity: str = "INFO",
    ) -> None:
        with self.sessions.begin() as session:
            session.add(
                AppEventRow(
                    cycle_id=cycle_id,
                    event_type=event_type,
                    severity=severity,
                    payload=payload,
                )
            )

    def unresolved_intents(self) -> list[dict[str, Any]]:
        terminal = {"FILLED", "REJECTED", "CANCELED", "SIMULATED"}
        with self.sessions() as session:
            rows = session.scalars(select(OrderIntentRow)).all()
            return [self._intent_dict(row) for row in rows if row.status not in terminal]

    def get_or_create_intent(
        self,
        intent_id: str,
        cloid: str,
        order: ApprovedOrder,
        status: str,
    ) -> tuple[dict[str, Any], bool]:
        with self.sessions() as session:
            existing = session.scalar(
                select(OrderIntentRow).where(
                    OrderIntentRow.decision_key == order.decision_key
                )
            )
            if existing is not None:
                return self._intent_dict(existing), False
            row = OrderIntentRow(
                intent_id=intent_id,
                cycle_id=order.cycle_id,
                decision_key=order.decision_key,
                cloid=cloid,
                symbol=order.symbol,
                action=order.action,
                direction=order.direction,
                notional_usd=Decimal(str(order.notional_usd)),
                status=status,
                payload=order.model_dump(mode="json"),
            )
            session.add(row)
            try:
                session.commit()
                return self._intent_dict(row), True
            except IntegrityError:
                session.rollback()
                existing = session.scalar(
                    select(OrderIntentRow).where(
                        OrderIntentRow.decision_key == order.decision_key
                    )
                )
                if existing is None:
                    raise
                return self._intent_dict(existing), False

    def mark_intent(self, intent_id: str, status: str) -> None:
        with self.sessions.begin() as session:
            row = session.get(OrderIntentRow, intent_id)
            if row is None:
                raise KeyError(intent_id)
            row.status = status

    def count_open_intents(self, cycle_id: str) -> int:
        with self.sessions() as session:
            rows = session.scalars(
                select(OrderIntentRow).where(
                    OrderIntentRow.cycle_id == cycle_id,
                    OrderIntentRow.action == "OPEN",
                )
            ).all()
            return len(rows)

    def ensure_protective_orders(
        self,
        parent_intent_id: str,
        cycle_id: str,
        specs: list[Any],
        status: str,
    ) -> list[dict[str, Any]]:
        with self.sessions.begin() as session:
            for spec in specs:
                if session.get(ProtectiveOrderRow, spec.protection_id) is not None:
                    continue
                session.add(
                    ProtectiveOrderRow(
                        protection_id=spec.protection_id,
                        parent_intent_id=parent_intent_id,
                        cycle_id=cycle_id,
                        cloid=spec.cloid,
                        symbol=spec.symbol,
                        direction=spec.direction,
                        kind=spec.kind,
                        level_index=spec.level_index,
                        trigger_px=Decimal(str(spec.trigger_px)),
                        size_fraction=Decimal(str(spec.size_fraction)),
                        original_notional_usd=Decimal(
                            str(spec.original_notional_usd)
                        ),
                        status=status,
                        payload=spec.model_dump(mode="json"),
                    )
                )
        return self.protective_orders(parent_intent_id=parent_intent_id)

    def protective_orders(
        self,
        *,
        symbol: str | None = None,
        parent_intent_id: str | None = None,
        statuses: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        with self.sessions() as session:
            query = select(ProtectiveOrderRow)
            if symbol is not None:
                query = query.where(ProtectiveOrderRow.symbol == symbol)
            if parent_intent_id is not None:
                query = query.where(
                    ProtectiveOrderRow.parent_intent_id == parent_intent_id
                )
            if statuses:
                query = query.where(ProtectiveOrderRow.status.in_(statuses))
            rows = session.scalars(
                query.order_by(
                    ProtectiveOrderRow.symbol,
                    ProtectiveOrderRow.kind,
                    ProtectiveOrderRow.level_index,
                )
            ).all()
            return [self._protective_dict(row) for row in rows]

    def mark_protection(self, protection_id: str, status: str) -> None:
        with self.sessions.begin() as session:
            row = session.get(ProtectiveOrderRow, protection_id)
            if row is None:
                raise KeyError(protection_id)
            row.status = status

    def cancel_protections(self, symbol: str, status: str = "CANCELED") -> None:
        active = {"PENDING", "ACTIVE", "UNKNOWN"}
        with self.sessions.begin() as session:
            rows = session.scalars(
                select(ProtectiveOrderRow).where(
                    ProtectiveOrderRow.symbol == symbol,
                    ProtectiveOrderRow.status.in_(active),
                )
            ).all()
            for row in rows:
                row.status = status

    def apply_paper_protection_triggers(
        self, marks: dict[str, float]
    ) -> list[dict[str, Any]]:
        triggered: list[dict[str, Any]] = []
        with self.sessions.begin() as session:
            rows = session.scalars(
                select(ProtectiveOrderRow)
                .where(ProtectiveOrderRow.status == "ACTIVE")
                .order_by(
                    ProtectiveOrderRow.symbol,
                    ProtectiveOrderRow.kind,
                    ProtectiveOrderRow.level_index,
                )
            ).all()
            by_symbol: dict[str, list[ProtectiveOrderRow]] = {}
            for row in rows:
                by_symbol.setdefault(row.symbol, []).append(row)

            for symbol, protections in by_symbol.items():
                position = session.get(PaperPositionRow, symbol)
                mark = marks.get(symbol)
                if position is None or mark is None:
                    if position is None:
                        for protection in protections:
                            protection.status = "CANCELED"
                    continue

                stop = next((p for p in protections if p.kind == "SL"), None)
                stop_hit = stop is not None and (
                    (stop.direction == "LONG" and mark <= float(stop.trigger_px))
                    or (stop.direction == "SHORT" and mark >= float(stop.trigger_px))
                )
                if stop_hit and stop is not None:
                    session.delete(position)
                    stop.status = "TRIGGERED"
                    for protection in protections:
                        if protection.protection_id != stop.protection_id:
                            protection.status = "CANCELED"
                    payload = {
                        "symbol": symbol,
                        "kind": "SL",
                        "trigger_px": float(stop.trigger_px),
                        "mark_px": mark,
                    }
                    session.add(
                        AppEventRow(
                            cycle_id=stop.cycle_id,
                            event_type="PROTECTION_TRIGGERED",
                            severity="WARN",
                            payload=payload,
                        )
                    )
                    triggered.append(payload)
                    continue

                take_profits = sorted(
                    (p for p in protections if p.kind == "TP"),
                    key=lambda item: item.level_index,
                )
                for take_profit in take_profits:
                    hit = (
                        take_profit.direction == "LONG"
                        and mark >= float(take_profit.trigger_px)
                    ) or (
                        take_profit.direction == "SHORT"
                        and mark <= float(take_profit.trigger_px)
                    )
                    if not hit:
                        continue
                    current_notional = float(position.notional_usd)
                    amount = min(
                        current_notional,
                        float(take_profit.original_notional_usd)
                        * float(take_profit.size_fraction),
                    )
                    remaining = current_notional - amount
                    take_profit.status = "TRIGGERED"
                    payload = {
                        "symbol": symbol,
                        "kind": "TP",
                        "level": take_profit.level_index,
                        "trigger_px": float(take_profit.trigger_px),
                        "mark_px": mark,
                        "closed_notional_usd": amount,
                    }
                    session.add(
                        AppEventRow(
                            cycle_id=take_profit.cycle_id,
                            event_type="PROTECTION_TRIGGERED",
                            severity="INFO",
                            payload=payload,
                        )
                    )
                    triggered.append(payload)
                    if remaining <= 0.01:
                        session.delete(position)
                        for protection in protections:
                            if protection.status == "ACTIVE":
                                protection.status = "CANCELED"
                        break
                    position.notional_usd = Decimal(str(remaining))
        return triggered

    def latest_filled_open_intent(self, symbol: str) -> dict[str, Any] | None:
        with self.sessions() as session:
            row = session.scalar(
                select(OrderIntentRow)
                .where(
                    OrderIntentRow.symbol == symbol,
                    OrderIntentRow.action == "OPEN",
                    OrderIntentRow.status == "FILLED",
                )
                .order_by(desc(OrderIntentRow.created_at))
                .limit(1)
            )
            return self._intent_dict(row) if row is not None else None

    def latest_playbook_plan(self, symbol: str) -> dict[str, Any] | None:
        with self.sessions() as session:
            cycles = session.scalars(
                select(CycleRow).order_by(desc(CycleRow.started_at)).limit(20)
            ).all()
            for cycle in cycles:
                plans = (
                    cycle.state.get("decision", {})
                    .get("playbook", {})
                    .get("payload", {})
                    .get("plans", [])
                )
                plan = next(
                    (item for item in plans if item.get("symbol") == symbol),
                    None,
                )
                if plan is not None:
                    return plan
        return None

    def positions(self) -> list[dict[str, Any]]:
        with self.sessions() as session:
            rows = session.scalars(select(PaperPositionRow).order_by(PaperPositionRow.symbol)).all()
            positions: list[dict[str, Any]] = []
            for row in rows:
                intent = session.scalar(
                    select(OrderIntentRow)
                    .where(
                        OrderIntentRow.symbol == row.symbol,
                        OrderIntentRow.action == "OPEN",
                        OrderIntentRow.status == "FILLED",
                    )
                    .order_by(desc(OrderIntentRow.created_at))
                    .limit(1)
                )
                leverage = int(intent.payload.get("leverage", 1)) if intent else 1
                targets = list(intent.payload.get("targets", [])) if intent else []
                if not targets:
                    target_rows = session.scalars(
                        select(ProtectiveOrderRow)
                        .where(
                            ProtectiveOrderRow.symbol == row.symbol,
                            ProtectiveOrderRow.kind == "TP",
                            ProtectiveOrderRow.status.in_(
                                {"PENDING", "ACTIVE", "TRIGGERED", "UNKNOWN"}
                            ),
                        )
                        .order_by(ProtectiveOrderRow.level_index)
                    ).all()
                    targets = [float(target.trigger_px) for target in target_rows]
                notional = float(row.notional_usd)
                positions.append({
                    "symbol": row.symbol,
                    "side": row.side,
                    "notional_usd": notional,
                    "leverage": leverage,
                    "margin_used_usd": notional / leverage,
                    "entry_px": float(row.entry_px),
                    "invalidation_px": float(row.invalidation_px),
                    "targets": targets,
                    "opened_at": _iso(row.opened_at),
                })
            return positions

    def open_paper_position(self, order: ApprovedOrder) -> None:
        with self.sessions.begin() as session:
            if session.get(PaperPositionRow, order.symbol) is not None:
                return
            session.add(
                PaperPositionRow(
                    symbol=order.symbol,
                    side=order.direction,
                    notional_usd=Decimal(str(order.notional_usd)),
                    entry_px=Decimal(str(order.mark_px)),
                    invalidation_px=Decimal(str(order.invalidation_px)),
                )
            )

    def close_paper_position(self, symbol: str) -> None:
        with self.sessions.begin() as session:
            row = session.get(PaperPositionRow, symbol)
            if row is not None:
                session.delete(row)

    def reduce_paper_position(self, symbol: str, fraction: float) -> None:
        with self.sessions.begin() as session:
            row = session.get(PaperPositionRow, symbol)
            if row is None:
                return
            remaining = Decimal(str(1.0 - fraction))
            if remaining <= 0:
                session.delete(row)
            else:
                row.notional_usd *= remaining

    def trade_history_context(self, limit: int = 2_000) -> dict[str, list[dict[str, Any]]]:
        """Return local enrichment data for the computed exchange trade view.

        Trade rows themselves are intentionally not persisted: rebuilding from
        immutable fills avoids a migration and makes refreshes idempotent.
        """

        with self.sessions() as session:
            intents = session.scalars(
                select(OrderIntentRow)
                .order_by(desc(OrderIntentRow.created_at))
                .limit(limit)
            ).all()
            protections = session.scalars(
                select(ProtectiveOrderRow)
                .order_by(desc(ProtectiveOrderRow.created_at))
                .limit(limit)
            ).all()
            cycles = session.scalars(
                select(CycleRow)
                .order_by(desc(CycleRow.started_at))
                .limit(limit)
            ).all()
        return {
            "intents": [self._intent_dict(row) for row in intents],
            "protections": [self._protective_dict(row) for row in protections],
            "cycles": [
                {
                    "cycle_id": row.cycle_id,
                    "started_at": _iso(row.started_at),
                    "state": row.state,
                }
                for row in cycles
            ],
        }

    def dashboard(self, limit: int = 20) -> dict[str, Any]:
        with self.sessions() as session:
            cycles = session.scalars(
                select(CycleRow).order_by(desc(CycleRow.started_at)).limit(limit)
            ).all()
            intents = session.scalars(
                select(OrderIntentRow).order_by(desc(OrderIntentRow.created_at)).limit(limit)
            ).all()
            events = session.scalars(
                select(AppEventRow).order_by(desc(AppEventRow.event_id)).limit(limit)
            ).all()
            llm_calls = session.scalars(
                select(LlmCallRow).order_by(desc(LlmCallRow.call_id)).limit(100)
            ).all()
        llm_payload = [self._llm_call_dict(row) for row in llm_calls]
        today = datetime.now(timezone.utc).date()
        return {
            "kill_switch": self.current_kill_switch().value,
            "cycles": [
                {
                    "cycle_id": row.cycle_id,
                    "mode": row.mode,
                    "status": row.status,
                    "started_at": _iso(row.started_at),
                    "finished_at": _iso(row.finished_at),
                    "state": row.state,
                    "error": row.error,
                }
                for row in cycles
            ],
            "intents": [self._intent_dict(row) for row in intents],
            "positions": self.positions(),
            "protections": self.protective_orders(),
            "events": [
                {
                    "event_id": row.event_id,
                    "cycle_id": row.cycle_id,
                    "event_type": row.event_type,
                    "severity": row.severity,
                    "payload": row.payload,
                    "created_at": _iso(row.created_at),
                }
                for row in events
            ],
            "llm_calls": llm_payload,
            "llm_costs": {
                "total_usd": sum(item["cost_usd"] for item in llm_payload),
                "today_usd": sum(
                    item["cost_usd"]
                    for item in llm_payload
                    if datetime.fromisoformat(item["created_at"]).date() == today
                ),
                "input_tokens": sum(item["input_tokens"] for item in llm_payload),
                "output_tokens": sum(item["output_tokens"] for item in llm_payload),
                "cached_tokens": sum(item["cached_tokens"] for item in llm_payload),
                "call_count": sum(1 for item in llm_payload if item["status"] == "COMPLETED"),
                "skipped_count": sum(1 for item in llm_payload if item["status"] == "SKIPPED"),
            },
        }

    def record_llm_call(self, payload: dict[str, Any]) -> None:
        with self.sessions.begin() as session:
            session.add(
                LlmCallRow(
                    cycle_id=payload.get("cycle_id"),
                    stage=str(payload.get("stage", "unknown")),
                    provider=str(payload.get("provider", "xai")),
                    model=str(payload.get("model", "unknown")),
                    status=str(payload.get("status", "COMPLETED")),
                    input_tokens=int(payload.get("input_tokens") or 0),
                    cached_tokens=int(payload.get("cached_tokens") or 0),
                    output_tokens=int(payload.get("output_tokens") or 0),
                    reasoning_tokens=int(payload.get("reasoning_tokens") or 0),
                    cost_usd=Decimal(str(payload.get("cost_usd") or 0)),
                    latency_ms=int(payload.get("latency_ms") or 0),
                    tool_usage=payload.get("tool_usage") or {},
                    prompt=payload.get("prompt") or {},
                    response=payload.get("response") or {},
                    skipped_reason=payload.get("skipped_reason"),
                )
            )

    def latest_completed_cycle(self) -> dict[str, Any] | None:
        with self.sessions() as session:
            row = session.scalar(
                select(CycleRow)
                .where(CycleRow.status == "COMPLETED")
                .order_by(desc(CycleRow.finished_at))
                .limit(1)
            )
            if row is None:
                return None
            return {
                "cycle_id": row.cycle_id,
                "finished_at": _iso(row.finished_at),
                "state": row.state,
            }

    @staticmethod
    def _intent_dict(row: OrderIntentRow) -> dict[str, Any]:
        leverage = int(row.payload.get("leverage", 1))
        return {
            "intent_id": row.intent_id,
            "cycle_id": row.cycle_id,
            "decision_key": row.decision_key,
            "cloid": row.cloid,
            "symbol": row.symbol,
            "action": row.action,
            "direction": row.direction,
            "notional_usd": float(row.notional_usd),
            "leverage": leverage,
            "margin_used_usd": float(row.notional_usd) / leverage,
            "status": row.status,
            "payload": row.payload,
            "created_at": _iso(row.created_at),
            "updated_at": _iso(row.updated_at),
        }

    @staticmethod
    def _protective_dict(row: ProtectiveOrderRow) -> dict[str, Any]:
        return {
            "protection_id": row.protection_id,
            "parent_intent_id": row.parent_intent_id,
            "cycle_id": row.cycle_id,
            "cloid": row.cloid,
            "symbol": row.symbol,
            "direction": row.direction,
            "kind": row.kind,
            "level_index": row.level_index,
            "trigger_px": float(row.trigger_px),
            "size_fraction": float(row.size_fraction),
            "original_notional_usd": float(row.original_notional_usd),
            "status": row.status,
            "payload": row.payload,
            "created_at": _iso(row.created_at),
            "updated_at": _iso(row.updated_at),
        }

    @staticmethod
    def _llm_call_dict(row: LlmCallRow) -> dict[str, Any]:
        return {
            "call_id": row.call_id,
            "cycle_id": row.cycle_id,
            "stage": row.stage,
            "provider": row.provider,
            "model": row.model,
            "status": row.status,
            "input_tokens": row.input_tokens,
            "cached_tokens": row.cached_tokens,
            "output_tokens": row.output_tokens,
            "reasoning_tokens": row.reasoning_tokens,
            "cost_usd": float(row.cost_usd),
            "latency_ms": row.latency_ms,
            "tool_usage": row.tool_usage,
            "prompt": row.prompt,
            "response": row.response,
            "skipped_reason": row.skipped_reason,
            "created_at": _iso(row.created_at),
        }
