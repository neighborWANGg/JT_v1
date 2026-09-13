import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class Case(Base):
    __tablename__ = "cases"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200))
    code: Mapped[str] = mapped_column(String(80), unique=True)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SourceFile(Base):
    __tablename__ = "source_files"
    __table_args__ = (UniqueConstraint("case_id", "sha256"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(String(64))
    stored_path: Mapped[str] = mapped_column(Text)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    case: Mapped[Case] = relationship()


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    case_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    source_file_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("source_files.id", ondelete="CASCADE"), index=True)
    source_row: Mapped[int] = mapped_column(Integer)
    sequence: Mapped[str | None] = mapped_column(String(80))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), index=True)
    group_id: Mapped[str | None] = mapped_column(String(120), index=True)
    sender_id: Mapped[str | None] = mapped_column(String(120), index=True)
    content: Mapped[str] = mapped_column(Text)
    ip_address: Mapped[str | None] = mapped_column(String(80))
    embedding: Mapped[list[float]] = mapped_column(JSON)


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("cases.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(200))
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    unread: Mapped[bool] = mapped_column(Boolean, default=False)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    section: Mapped[str] = mapped_column(String(40), default="默认")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Analysis(Base):
    __tablename__ = "analyses"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    question: Mapped[str] = mapped_column(Text)
    result: Mapped[dict] = mapped_column(JSON)
    engine: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AnalysisTask(Base):
    __tablename__ = "analysis_tasks"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    request: Mapped[dict] = mapped_column(JSON)
    analysis_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("analyses.id", ondelete="SET NULL"))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Review(Base):
    __tablename__ = "reviews"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    analysis_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("analyses.id", ondelete="CASCADE"), index=True
    )
    decision: Mapped[str] = mapped_column(String(20))
    reviewer: Mapped[str] = mapped_column(String(100))
    comment: Mapped[str] = mapped_column(Text, default="")
    revised_result: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    action: Mapped[str] = mapped_column(String(80), index=True)
    target_type: Mapped[str] = mapped_column(String(40))
    target_id: Mapped[str] = mapped_column(String(80))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
