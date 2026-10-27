from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import String, Integer, Float, DateTime, JSON, ForeignKey, Boolean, CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base


def uid():
    return str(uuid4())


def now():
    return datetime.now(timezone.utc)


class AudioFile(Base):
    __tablename__ = "audio_files"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(255))
    storage_key: Mapped[str] = mapped_column(String(100), unique=True)
    size: Mapped[int] = mapped_column(Integer)
    metadata_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    analysis: Mapped["AudioAnalysis | None"] = relationship(cascade="all, delete-orphan", uselist=False)
    fingerprint: Mapped["AudioFingerprint | None"] = relationship(cascade="all, delete-orphan", uselist=False)
    jobs: Mapped[list["ProcessingJob"]] = relationship(cascade="all, delete-orphan", passive_deletes=True)
    versions: Mapped[list["ProcessedVersion"]] = relationship(
        cascade="all, delete-orphan", passive_deletes=True
    )


class AudioAnalysis(Base):
    __tablename__ = "audio_analyses"
    audio_id: Mapped[str] = mapped_column(ForeignKey("audio_files.id", ondelete="CASCADE"), primary_key=True)
    data: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AudioFingerprint(Base):
    __tablename__ = "audio_fingerprints"
    audio_id: Mapped[str] = mapped_column(ForeignKey("audio_files.id", ondelete="CASCADE"), primary_key=True)
    data: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ProcessingJob(Base):
    __tablename__ = "processing_jobs"
    __table_args__ = (
        CheckConstraint("state IN ('pending','queued','processing','completed','failed','cancelled')"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    audio_id: Mapped[str] = mapped_column(ForeignKey("audio_files.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    state: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    cache_key: Mapped[str | None] = mapped_column(String(64), index=True)
    progress: Mapped[float] = mapped_column(Float, default=0)
    phase: Mapped[str] = mapped_column(String(80), default="Waiting for worker")
    error: Mapped[str | None] = mapped_column(String(500))
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    version_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now, onupdate=now, index=True
    )


class ProcessedVersion(Base):
    __tablename__ = "processed_versions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    audio_id: Mapped[str] = mapped_column(ForeignKey("audio_files.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("processing_jobs.id", ondelete="CASCADE"), unique=True)
    storage_key: Mapped[str] = mapped_column(String(100), unique=True)
    kind: Mapped[str] = mapped_column(String(16))
    params: Mapped[dict] = mapped_column(JSON)
    analysis: Mapped[dict] = mapped_column(JSON)
    fingerprint: Mapped[dict] = mapped_column(JSON)
    comparison: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ProcessingPreset(Base):
    __tablename__ = "processing_presets"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(100))
    params: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
