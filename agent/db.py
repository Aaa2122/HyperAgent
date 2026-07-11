from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, Numeric, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class CycleRow(Base):
    __tablename__ = "cycles"

    cycle_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    state: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)


class KillSwitchEventRow(Base):
    __tablename__ = "kill_switch_events"

    event_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    state: Mapped[str] = mapped_column(String(20), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class OrderIntentRow(Base):
    __tablename__ = "order_intents"

    intent_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    cycle_id: Mapped[str] = mapped_column(
        ForeignKey("cycles.cycle_id"), nullable=False, index=True
    )
    decision_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    cloid: Mapped[str] = mapped_column(String(34), nullable=False, unique=True)
    symbol: Mapped[str] = mapped_column(String(12), nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    notional_usd: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )


class PaperPositionRow(Base):
    __tablename__ = "paper_positions"

    symbol: Mapped[str] = mapped_column(String(12), primary_key=True)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    notional_usd: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    entry_px: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    invalidation_px: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class ProtectiveOrderRow(Base):
    __tablename__ = "protective_orders"

    protection_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    parent_intent_id: Mapped[str] = mapped_column(
        ForeignKey("order_intents.intent_id"), nullable=False, index=True
    )
    cycle_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    cloid: Mapped[str] = mapped_column(String(34), nullable=False, unique=True)
    symbol: Mapped[str] = mapped_column(String(12), nullable=False, index=True)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    level_index: Mapped[int] = mapped_column(Integer, nullable=False)
    trigger_px: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    size_fraction: Mapped[Decimal] = mapped_column(Numeric(12, 8), nullable=False)
    original_notional_usd: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )


class AppEventRow(Base):
    __tablename__ = "app_events"

    event_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    cycle_id: Mapped[str | None] = mapped_column(String(36), index=True)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    severity: Mapped[str] = mapped_column(String(12), nullable=False, default="INFO")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class LlmCallRow(Base):
    __tablename__ = "llm_calls"

    call_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    cycle_id: Mapped[str | None] = mapped_column(String(36), index=True)
    stage: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="xai")
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cached_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reasoning_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(18, 10), nullable=False, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tool_usage: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    prompt: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    response: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    skipped_reason: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


def build_engine(database_url: str):
    kwargs: dict[str, Any] = {"pool_pre_ping": True}
    if database_url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if database_url in {"sqlite://", "sqlite:///:memory:"}:
            kwargs["poolclass"] = StaticPool
    return create_engine(database_url, **kwargs)


def build_session_factory(engine):
    return sessionmaker(bind=engine, expire_on_commit=False)
