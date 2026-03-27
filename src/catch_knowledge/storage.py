from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from catch_knowledge.db.models import KBDocument, PostAnalysis, RawPost
from catch_knowledge.domain import CollectedPost, StructuredAnalysis


def compute_content_hash(text: Optional[str], fallback_url: str) -> str:
    base = text or fallback_url
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def upsert_raw_post(session: Session, post: CollectedPost) -> Tuple[RawPost, bool]:
    existing = session.scalar(
        select(RawPost).where(
            RawPost.platform == post.platform,
            RawPost.post_id == post.post_id,
        )
    )
    content_hash = compute_content_hash(post.raw_text, post.url)
    if existing:
        existing.url = post.url
        existing.title = post.title
        existing.author_name = post.author_name
        existing.published_at = post.published_at
        existing.raw_html = post.raw_html
        existing.raw_text = post.raw_text
        existing.metadata_json = post.metadata_json
        existing.content_hash = content_hash
        return existing, False

    created = RawPost(
        platform=post.platform,
        post_id=post.post_id,
        url=post.url,
        title=post.title,
        author_name=post.author_name,
        published_at=post.published_at,
        raw_html=post.raw_html,
        raw_text=post.raw_text,
        metadata_json=post.metadata_json,
        content_hash=content_hash,
        status="collected",
    )
    session.add(created)
    session.flush()
    return created, True


def save_analysis(session: Session, raw_post: RawPost, analysis: StructuredAnalysis, model: str) -> PostAnalysis:
    existing = session.scalar(select(PostAnalysis).where(PostAnalysis.raw_post_id == raw_post.id))
    payload = dict(
        is_interview_experience=analysis.is_interview_experience,
        company=analysis.company,
        job_role=analysis.job_role,
        job_direction=analysis.job_direction,
        interview_rounds=analysis.interview_rounds,
        tags=analysis.tags,
        question_points=analysis.question_points,
        summary=analysis.summary,
        difficulty=analysis.difficulty,
        normalized_json=analysis.normalized_json,
        llm_model=model,
    )
    if existing:
        for key, value in payload.items():
            setattr(existing, key, value)
        raw_post.status = "processed"
        return existing

    created = PostAnalysis(raw_post_id=raw_post.id, **payload)
    session.add(created)
    raw_post.status = "processed"
    session.flush()
    return created


def save_kb_document(session: Session, raw_post: RawPost, title: str, path: Path) -> KBDocument:
    existing = session.scalar(select(KBDocument).where(KBDocument.raw_post_id == raw_post.id))
    if existing:
        existing.doc_title = title
        existing.markdown_path = str(path)
        return existing

    created = KBDocument(raw_post_id=raw_post.id, doc_title=title, markdown_path=str(path))
    session.add(created)
    session.flush()
    return created
