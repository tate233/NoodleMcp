from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class RawPost(Base):
    __tablename__ = "raw_posts"
    __table_args__ = (
        UniqueConstraint("platform", "post_id", name="uq_raw_posts_platform_post_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(String(64), index=True)
    post_id: Mapped[str] = mapped_column(String(255))
    url: Mapped[str] = mapped_column(Text, unique=True)
    title: Mapped[Optional[str]] = mapped_column(Text)
    author_name: Mapped[Optional[str]] = mapped_column(String(255))
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    crawled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    raw_html: Mapped[Optional[str]] = mapped_column(Text)
    raw_text: Mapped[Optional[str]] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), default="collected", index=True)
    metadata_json: Mapped[Optional[Dict]] = mapped_column(JSON)

    analysis: Mapped[Optional["PostAnalysis"]] = relationship(back_populates="raw_post", uselist=False)
    kb_document: Mapped[Optional["KBDocument"]] = relationship(back_populates="raw_post", uselist=False)


class PostAnalysis(Base):
    __tablename__ = "post_analysis"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    raw_post_id: Mapped[int] = mapped_column(ForeignKey("raw_posts.id"), unique=True, index=True)
    is_interview_experience: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    company: Mapped[Optional[str]] = mapped_column(String(255), index=True)
    job_role: Mapped[Optional[str]] = mapped_column(String(255), index=True)
    job_direction: Mapped[Optional[str]] = mapped_column(String(255), index=True)
    interview_rounds: Mapped[Optional[List[str]]] = mapped_column(JSON)
    tags: Mapped[Optional[List[str]]] = mapped_column(JSON)
    question_points: Mapped[Optional[List[str]]] = mapped_column(JSON)
    summary: Mapped[Optional[str]] = mapped_column(Text)
    difficulty: Mapped[Optional[str]] = mapped_column(String(64))
    normalized_json: Mapped[Optional[Dict]] = mapped_column(JSON)
    llm_model: Mapped[Optional[str]] = mapped_column(String(128))
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    raw_post: Mapped[RawPost] = relationship(back_populates="analysis")


class KBDocument(Base):
    __tablename__ = "kb_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    raw_post_id: Mapped[int] = mapped_column(ForeignKey("raw_posts.id"), unique=True, index=True)
    doc_title: Mapped[str] = mapped_column(String(255))
    markdown_path: Mapped[str] = mapped_column(Text, unique=True)
    embedding_status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    raw_post: Mapped[RawPost] = relationship(back_populates="kb_document")
