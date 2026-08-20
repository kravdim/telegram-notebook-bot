"""SQLAlchemy модели всех таблиц."""

import uuid
from datetime import date, datetime, time
from typing import List, Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False, default="user")
    timezone: Mapped[str] = mapped_column(Text, nullable=False, default="Europe/Moscow")
    digest_morning_time: Mapped[time] = mapped_column(Time, nullable=False, default=time(8, 0))
    digest_evening_time: Mapped[time] = mapped_column(Time, nullable=False, default=time(21, 0))
    memoir_prompt_time: Mapped[time] = mapped_column(Time, nullable=False, default=time(20, 45))
    digest_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    chronometry_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    chronometry_interval_min: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    work_start_time: Mapped[time] = mapped_column(Time, nullable=False, default=time(9, 0))
    work_end_time: Mapped[time] = mapped_column(Time, nullable=False, default=time(18, 0))
    work_days: Mapped[List[int]] = mapped_column(ARRAY(Integer), nullable=False, default=lambda: [1, 2, 3, 4, 5])
    focus_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    onboarding_completed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    digest_sent_date: Mapped[Optional[date]] = mapped_column(Date)
    digest_evening_sent_date: Mapped[Optional[date]] = mapped_column(Date)
    memoir_asked_date: Mapped[Optional[date]] = mapped_column(Date)
    chronometry_last_asked: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    tasks_reminder_last_hour: Mapped[Optional[int]] = mapped_column(Integer)
    tasks_reminder_last_date: Mapped[Optional[date]] = mapped_column(Date)
    weekly_review_sent_date: Mapped[Optional[date]] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )

    __table_args__ = (
        CheckConstraint("role IN ('admin', 'user')", name="ck_users_role"),
    )


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.telegram_id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    category: Mapped[str] = mapped_column(Text, nullable=False, default="work")
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )

    __table_args__ = (
        CheckConstraint("category IN ('work', 'personal')", name="ck_projects_category"),
        CheckConstraint("status IN ('active', 'paused', 'done')", name="ck_projects_status"),
    )


class Trip(Base):
    __tablename__ = "trips"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.telegram_id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    destination: Mapped[Optional[str]] = mapped_column(Text)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    timezone: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )

    __table_args__ = (
        CheckConstraint("status IN ('active', 'completed')", name="ck_trips_status"),
    )


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.telegram_id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL")
    )
    trip_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("trips.id", ondelete="SET NULL")
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False, default="work")
    priority: Mapped[str] = mapped_column(Text, nullable=False, default="normal")
    is_frog: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    scheduled_date: Mapped[Optional[date]] = mapped_column(Date)
    due_date: Mapped[Optional[date]] = mapped_column(Date)
    due_time: Mapped[Optional[time]] = mapped_column(Time)
    remind_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    remind_before_min: Mapped[Optional[int]] = mapped_column(Integer)
    repeat_rule: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="open")
    resolution: Mapped[Optional[str]] = mapped_column(Text)
    tags: Mapped[List[str]] = mapped_column(ARRAY(Text), default=[])
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": version}

    __table_args__ = (
        CheckConstraint("category IN ('work', 'personal')", name="ck_tasks_category"),
        CheckConstraint("priority IN ('high', 'medium', 'normal')", name="ck_tasks_priority"),
        CheckConstraint("status IN ('open', 'done', 'cancelled')", name="ck_tasks_status"),
        CheckConstraint(
            "resolution IN ('completed', 'cancelled', 'deferred', 'expired')",
            name="ck_tasks_resolution",
        ),
        Index("idx_tasks_user_status", "user_id", "status"),
        Index("idx_tasks_scheduled_date", "scheduled_date", postgresql_where="status = 'open'"),
        Index("idx_tasks_due_date", "due_date", postgresql_where="status = 'open'"),
        Index("idx_tasks_frog", "user_id", postgresql_where="is_frog = TRUE AND status = 'open'"),
    )


class InteractionState(Base):
    """Persisted user interaction state for multi-step flows."""

    __tablename__ = "interaction_states"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.telegram_id", ondelete="CASCADE"), primary_key=True
    )
    state_type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )

    __table_args__ = (
        Index("idx_interaction_states_expires", "expires_at"),
    )


class FsmState(Base):
    """Персистентное состояние aiogram FSM."""

    __tablename__ = "fsm_states"

    storage_key: Mapped[str] = mapped_column(Text, primary_key=True)
    state: Mapped[Optional[str]] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )


class ProcessedRequest(Base):
    """Идемпотентность входящих Telegram-сообщений."""

    __tablename__ = "processed_requests"

    request_key: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.telegram_id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, default="processing")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "status IN ('processing', 'completed', 'failed')",
            name="ck_processed_requests_status",
        ),
    )


class OperationalState(Base):
    """Persistent operational markers and SLO state."""

    __tablename__ = "operational_state"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )


class MemoirEntry(Base):
    __tablename__ = "memoir_entries"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.telegram_id", ondelete="CASCADE"), nullable=False
    )
    event_date: Mapped[date] = mapped_column(Date, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    value_tag: Mapped[Optional[str]] = mapped_column(Text)
    period_type: Mapped[str] = mapped_column(Text, nullable=False, default="day")
    embedding = mapped_column(Vector(768))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )

    __table_args__ = (
        CheckConstraint(
            "period_type IN ('day', 'week', 'month', 'year')",
            name="ck_memoir_period_type",
        ),
        UniqueConstraint("user_id", "event_date", "period_type", name="uq_memoir_user_date_period"),
        Index(
            "idx_memoir_content_trgm", "content",
            postgresql_using="gin",
            postgresql_ops={"content": "gin_trgm_ops"},
        ),
    )


class TimeTrackingEntry(Base):
    __tablename__ = "time_tracking_entries"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.telegram_id", ondelete="CASCADE"), nullable=False
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    activity_text: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    is_planned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    matched_task_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="SET NULL")
    )
    productivity_score: Mapped[Optional[int]] = mapped_column(Integer)
    bot_reaction: Mapped[Optional[str]] = mapped_column(Text)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=15)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )

    __table_args__ = (
        CheckConstraint(
            "category IN ('work', 'personal', 'rest', 'waste', 'focus', 'unknown')",
            name="ck_time_tracking_category",
        ),
        CheckConstraint(
            "productivity_score BETWEEN 1 AND 5",
            name="ck_time_tracking_score",
        ),
    )


class Note(Base):
    __tablename__ = "notes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.telegram_id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[Optional[str]] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[List[str]] = mapped_column(ARRAY(Text), default=[])
    embedding = mapped_column(Vector(768))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )

    __table_args__ = (
        Index(
            "idx_notes_content_trgm", "content",
            postgresql_using="gin",
            postgresql_ops={"content": "gin_trgm_ops"},
        ),
    )


class DiaryEntry(Base):
    __tablename__ = "diary_entries"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.telegram_id", ondelete="CASCADE"), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    entry_date: Mapped[date] = mapped_column(Date, nullable=False)
    embedding = mapped_column(Vector(768))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )

    __table_args__ = (
        Index(
            "idx_diary_content_trgm", "content",
            postgresql_using="gin",
            postgresql_ops={"content": "gin_trgm_ops"},
        ),
    )


class Reminder(Base):
    __tablename__ = "reminders"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.telegram_id", ondelete="CASCADE"), nullable=False
    )
    task_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE")
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    remind_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    repeat_rule: Mapped[Optional[str]] = mapped_column(Text)
    is_sent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    series_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, default=uuid.uuid4
    )
    occurrence_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    snooze_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    delivery_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'delivered', 'snoozed', 'resolved', 'cancelled')",
            name="ck_reminders_status",
        ),
        UniqueConstraint("series_id", "occurrence_at", name="uq_reminder_series_occurrence"),
        Index("idx_reminders_pending", "remind_at", postgresql_where="is_sent = FALSE"),
    )


class Birthday(Base):
    """Дни рождения контактов."""
    __tablename__ = "birthdays"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.telegram_id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    birth_date: Mapped[date] = mapped_column(Date, nullable=False)
    year_known: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    note: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )

    __table_args__ = (
        Index("idx_birthdays_user_date", "user_id", "birth_date"),
    )


class KnowledgeChunk(Base):
    """Чанки базы знаний (выжимки из книг Архангельского)."""
    __tablename__ = "knowledge_base"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source: Mapped[str] = mapped_column(Text, nullable=False)
    topic: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding = mapped_column(Vector(768))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )

    __table_args__ = (
        Index(
            "idx_kb_content_trgm", "content",
            postgresql_using="gin",
            postgresql_ops={"content": "gin_trgm_ops"},
        ),
    )


class PromptVersion(Base):
    __tablename__ = "prompt_versions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    prompt_key: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )

    __table_args__ = (
        UniqueConstraint("prompt_key", "version", name="uq_prompt_key_version"),
        Index(
            "idx_prompt_active", "prompt_key",
            unique=True,
            postgresql_where="is_active = TRUE",
        ),
    )


class LlmLog(Base):
    __tablename__ = "llm_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("users.telegram_id", ondelete="SET NULL")
    )
    prompt_key: Mapped[Optional[str]] = mapped_column(Text)
    prompt_version: Mapped[Optional[int]] = mapped_column(Integer)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    input_messages: Mapped[dict] = mapped_column(JSONB, nullable=False)
    output_content: Mapped[Optional[str]] = mapped_column(Text)
    function_call: Mapped[Optional[dict]] = mapped_column(JSONB)
    total_tokens: Mapped[Optional[int]] = mapped_column(Integer)
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer)
    error: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )

    __table_args__ = (
        Index("idx_llm_logs_key_time", "prompt_key", "created_at"),
    )
