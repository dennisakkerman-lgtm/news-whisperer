"""Storage: Postgres on Railway (DATABASE_URL), SQLite locally."""
from __future__ import annotations

import os
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from whisperer.core import ROOT


def _url() -> str:
    url = os.environ.get("DATABASE_URL", f"sqlite:///{ROOT / 'data' / 'whisperer.db'}")
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


engine = create_engine(_url(), pool_pre_ping=True)
Session = sessionmaker(engine, expire_on_commit=False)


def now():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Run(Base):
    __tablename__ = "runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client: Mapped[str] = mapped_column(String(50))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    config_version: Mapped[str] = mapped_column(String(50), default="")
    lookback_days: Mapped[int] = mapped_column(Integer, default=7)
    status: Mapped[str] = mapped_column(String(20), default="running")
    stats: Mapped[dict] = mapped_column(JSON, default=dict)       # per-source counts + errors
    report_html: Mapped[str] = mapped_column(Text, default="")
    opportunities = relationship("Opportunity", back_populates="run", cascade="all, delete-orphan")


class Opportunity(Base):
    __tablename__ = "opportunities"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    bucket: Mapped[str] = mapped_column(String(20), index=True)   # this_week | monitor | skip
    title: Mapped[str] = mapped_column(Text)
    signal_title: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(30))
    layer: Mapped[int] = mapped_column(Integer)
    format: Mapped[str] = mapped_column(String(20))
    urgency: Mapped[str] = mapped_column(String(20), default="")
    audience: Mapped[int] = mapped_column(Integer)
    timeliness: Mapped[int] = mapped_column(Integer)
    gap: Mapped[int] = mapped_column(Integer)
    testability: Mapped[int] = mapped_column(Integer)
    total: Mapped[int] = mapped_column(Integer, index=True)
    narrative_score: Mapped[int] = mapped_column(Integer, nullable=True)
    keyword: Mapped[str] = mapped_column(Text, default="")
    rationale: Mapped[str] = mapped_column(Text, default="")
    skip_reason: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    city: Mapped[str] = mapped_column(String(50), nullable=True)
    personas: Mapped[list] = mapped_column(JSON, default=list)
    gap_prompt: Mapped[str] = mapped_column(Text, nullable=True)
    gap_visibility: Mapped[float] = mapped_column(nullable=True)
    published: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    # internal curation (Seeders team only)
    status: Mapped[str] = mapped_column(String(20), default="new")  # new | in_progress | actioned | skipped
    notes: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    run = relationship("Run", back_populates="opportunities")


def init_db():
    (ROOT / "data").mkdir(exist_ok=True)
    Base.metadata.create_all(engine)
