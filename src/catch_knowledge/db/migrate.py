from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from catch_knowledge.config import Settings

from .models import KBDocument, PostAnalysis, RawPost
from .session import create_session_factory, create_tables


def migrate_sqlite_to_current_db(settings: Settings, sqlite_path: Path) -> dict:
    create_tables(settings)
    session_factory = create_session_factory(settings)

    source = sqlite3.connect(str(sqlite_path))
    source.row_factory = sqlite3.Row

    stats = {"raw_posts": 0, "post_analysis": 0, "kb_documents": 0}
    try:
        with session_factory() as session:
            session: Session
            _migrate_raw_posts(source, session, stats)
            _migrate_post_analysis(source, session, stats)
            _migrate_kb_documents(source, session, stats)
            session.commit()
    finally:
        source.close()

    return stats


def _migrate_raw_posts(source, session: Session, stats: dict) -> None:
    rows = source.execute("select * from raw_posts order by id asc").fetchall()
    for row in rows:
        existing = session.get(RawPost, row["id"])
        payload = RawPost(
            id=row["id"],
            platform=row["platform"],
            post_id=row["post_id"],
            url=row["url"],
            title=row["title"],
            author_name=row["author_name"],
            published_at=_coerce_datetime(row["published_at"]),
            crawled_at=_coerce_datetime(row["crawled_at"]),
            raw_html=row["raw_html"],
            raw_source_text=_coerce_nullable(row, "raw_source_text"),
            raw_image_text=_coerce_nullable(row, "raw_image_text"),
            raw_text=row["raw_text"],
            image_urls=_coerce_json_list(_coerce_nullable(row, "image_urls")),
            content_hash=row["content_hash"],
            status=row["status"],
            metadata_json=_coerce_json_dict(row["metadata_json"]),
        )
        if existing:
            _copy_model_fields(existing, payload)
        else:
            session.add(payload)
        stats["raw_posts"] += 1


def _migrate_post_analysis(source, session: Session, stats: dict) -> None:
    rows = source.execute("select * from post_analysis order by id asc").fetchall()
    for row in rows:
        existing = session.get(PostAnalysis, row["id"])
        payload = PostAnalysis(
            id=row["id"],
            raw_post_id=row["raw_post_id"],
            is_interview_experience=bool(row["is_interview_experience"]),
            company=row["company"],
            job_role=row["job_role"],
            job_direction=row["job_direction"],
            interview_rounds=_coerce_json_list(row["interview_rounds"]),
            tags=_coerce_json_list(row["tags"]),
            interview_questions=_coerce_json_list(_coerce_nullable(row, "interview_questions")),
            question_points=_coerce_json_list(row["question_points"]),
            summary=row["summary"],
            difficulty=row["difficulty"],
            normalized_json=_coerce_json_dict(row["normalized_json"]),
            llm_model=row["llm_model"],
            processed_at=_coerce_datetime(row["processed_at"]),
        )
        if existing:
            _copy_model_fields(existing, payload)
        else:
            session.add(payload)
        stats["post_analysis"] += 1


def _migrate_kb_documents(source, session: Session, stats: dict) -> None:
    rows = source.execute("select * from kb_documents order by id asc").fetchall()
    for row in rows:
        existing = session.get(KBDocument, row["id"])
        payload = KBDocument(
            id=row["id"],
            raw_post_id=row["raw_post_id"],
            doc_title=row["doc_title"],
            markdown_path=row["markdown_path"],
            embedding_status=row["embedding_status"],
            created_at=_coerce_datetime(row["created_at"]),
        )
        if existing:
            _copy_model_fields(existing, payload)
        else:
            session.add(payload)
        stats["kb_documents"] += 1


def _copy_model_fields(target, source) -> None:
    for column in source.__table__.columns:
        setattr(target, column.name, getattr(source, column.name))


def _coerce_nullable(row: sqlite3.Row, key: str):
    return row[key] if key in row.keys() else None


def _coerce_json_list(value: Any):
    if value in (None, "", "null"):
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return []
    return []


def _coerce_json_dict(value: Any):
    if value in (None, "", "null"):
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _coerce_datetime(value: Any):
    if value in (None, "", "null"):
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        text = text.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
                try:
                    return datetime.strptime(text, fmt)
                except ValueError:
                    continue
    return None
