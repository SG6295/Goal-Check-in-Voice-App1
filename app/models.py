"""SQLAlchemy models for the complete first-stage database schema."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    phone_e164: Mapped[str] = mapped_column(String(16), unique=True, nullable=False)
    persona: Mapped[str] = mapped_column(String(500), nullable=False)
    consent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consent_text_version: Mapped[str] = mapped_column(String(20), nullable=False)
    consent_ip: Mapped[str | None] = mapped_column(String(45))
    opted_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Goal(Base):
    __tablename__ = "goals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), unique=True, nullable=False
    )
    title: Mapped[str | None] = mapped_column(String(120))
    done_definition: Mapped[str | None] = mapped_column(String(500))
    deadline: Mapped[date | None] = mapped_column(Date)
    call_lock_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Milestone(Base):
    __tablename__ = "milestones"
    __table_args__ = (
        UniqueConstraint("goal_id", "position", name="uq_milestones_goal_position"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    goal_id: Mapped[int] = mapped_column(ForeignKey("goals.id"))
    position: Mapped[int] = mapped_column(SmallInteger)
    title: Mapped[str | None] = mapped_column(String(80))
    due_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str | None] = mapped_column(
        String(16), server_default=text("'not_started'")
    )
    status_source: Mapped[str | None] = mapped_column(
        String(12), server_default=text("'user'")
    )
    status_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Call(Base):
    __tablename__ = "calls"
    __table_args__ = (
        Index("ix_calls_goal_id_requested_at", "goal_id", "requested_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    goal_id: Mapped[int | None] = mapped_column(ForeignKey("goals.id"))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    mode: Mapped[str | None] = mapped_column(String(8))
    state: Mapped[str | None] = mapped_column(String(16))
    conversation_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    sip_call_id: Mapped[str | None] = mapped_column(String(64))
    milestone_1_id: Mapped[int | None] = mapped_column(ForeignKey("milestones.id"))
    milestone_2_id: Mapped[int | None] = mapped_column(ForeignKey("milestones.id"))
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_reason: Mapped[str | None] = mapped_column(Text)
    duration_secs: Mapped[int | None] = mapped_column(Integer)
    transcript: Mapped[dict[str, Any] | list[Any] | None] = mapped_column(JSONB)
    summary_note: Mapped[str | None] = mapped_column(Text)
    m1_status_raw: Mapped[str | None] = mapped_column(Text)
    m2_status_raw: Mapped[str | None] = mapped_column(Text)
    m1_status: Mapped[str | None] = mapped_column(String(16))
    m2_status: Mapped[str | None] = mapped_column(String(16))
    blocker: Mapped[str | None] = mapped_column(Text)
    opt_out: Mapped[bool | None] = mapped_column(
        Boolean, server_default=text("false")
    )


class CallCorrection(Base):
    __tablename__ = "call_corrections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    call_id: Mapped[UUID | None] = mapped_column(ForeignKey("calls.id"))
    field: Mapped[str | None] = mapped_column(String(32))
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class WebhookEvent(Base):
    __tablename__ = "webhook_events"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id", "event_type", name="uq_webhook_events_conversation_type"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[str | None] = mapped_column(String(64))
    event_type: Mapped[str | None] = mapped_column(String(40))
    event_timestamp: Mapped[int | None] = mapped_column(Integer)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
