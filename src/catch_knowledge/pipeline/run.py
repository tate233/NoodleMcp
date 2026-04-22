from __future__ import annotations

import json

import time
from pathlib import Path

from sqlalchemy.orm import Session

from catch_knowledge.config import Settings
from catch_knowledge.db import create_session_factory, create_tables
from catch_knowledge.db.models import PostAnalysis, RawPost, TaxonomySuggestion
from catch_knowledge.domain import CollectedPost
from catch_knowledge.exporters import MarkdownExporter
from catch_knowledge.indexing import QuestionIndexBuilder
from catch_knowledge.llm import LLMAnalyzer
from catch_knowledge.manual_import import ManualImportRequest, build_manual_post
from catch_knowledge.ocr import VolcengineOCRProcessor
from catch_knowledge.sources import NowcoderCollector, XiaohongshuMCPCollector
from catch_knowledge.storage import save_analysis, save_kb_document, upsert_raw_post


def run_pipeline(settings: Settings) -> dict:
    create_tables(settings)
    session_factory = create_session_factory(settings)
    collector = _build_collector(settings)
    ocr_processor = _build_ocr_processor(settings)

    stats = {
        "collected": 0,
        "ocr_enriched": 0,
        "new_posts": 0,
        "processed": 0,
        "exported": 0,
        "analysis_failed": 0,
    }
    posts = collector.collect()
    stats["collected"] = len(posts)

    raw_post_ids = []
    with session_factory() as session:
        session: Session
        for post in posts:
            if ocr_processor:
                enriched_post = ocr_processor.enrich_post(post)
                if enriched_post.raw_image_text:
                    stats["ocr_enriched"] += 1
                post = enriched_post
            raw_post, is_new = upsert_raw_post(session, post)
            if is_new:
                stats["new_posts"] += 1
            raw_post_ids.append(raw_post.id)
            if not raw_post.raw_text:
                raw_post.status = "detail_failed"
            session.commit()

    analysis_stats = analyze_raw_posts(settings, raw_post_ids=raw_post_ids)
    for key, value in analysis_stats.items():
        stats[key] = stats.get(key, 0) + value

    return stats


def analyze_raw_posts(settings: Settings, raw_post_ids=None) -> dict:
    create_tables(settings)
    session_factory = create_session_factory(settings)
    analyzer = LLMAnalyzer(settings)
    exporter = MarkdownExporter(settings)
    stats = {"processed": 0, "exported": 0, "analysis_failed": 0}

    with session_factory() as session:
        session: Session
        query = session.query(RawPost)
        if raw_post_ids:
            query = query.filter(RawPost.id.in_(raw_post_ids))
        else:
            query = query.filter(RawPost.status.in_(["collected", "analysis_failed", "analysis_fallback"]))

        for raw_post in query.order_by(RawPost.id.asc()).all():
            if not raw_post.raw_text:
                continue

            try:
                analysis_payload = analyzer.analyze(raw_post.title, raw_post.raw_text)
                analysis = save_analysis(session, raw_post, analysis_payload, settings.openai_model)
                stats["processed"] += 1

                if analysis.is_interview_experience:
                    title, path = exporter.export(raw_post, analysis)
                    save_kb_document(session, raw_post, title, path)
                    stats["exported"] += 1

                session.commit()
                time.sleep(1)
            except Exception:
                raw_post.status = "analysis_failed"
                session.commit()
                stats["analysis_failed"] += 1

    return stats


def reanalyze_fallback_posts(settings: Settings) -> dict:
    create_tables(settings)
    session_factory = create_session_factory(settings)

    with session_factory() as session:
        session: Session
        rows = (
            session.query(RawPost)
            .outerjoin(PostAnalysis, PostAnalysis.raw_post_id == RawPost.id)
            .filter(
                (RawPost.status == "analysis_fallback")
                | (PostAnalysis.normalized_json.like('%"llm_fallback": true%'))
            )
            .all()
        )
        ids = [row.id for row in rows]

    return analyze_raw_posts(settings, raw_post_ids=ids)


def reanalyze_missing_questions(settings: Settings) -> dict:
    create_tables(settings)
    session_factory = create_session_factory(settings)

    with session_factory() as session:
        session: Session
        rows = (
            session.query(RawPost)
            .join(PostAnalysis, PostAnalysis.raw_post_id == RawPost.id)
            .filter(
                (PostAnalysis.interview_questions.is_(None))
                | (PostAnalysis.interview_questions == "[]")
            )
            .all()
        )
        ids = [row.id for row in rows]

    return analyze_raw_posts(settings, raw_post_ids=ids)


def rerun_ocr_posts(settings: Settings) -> dict:
    create_tables(settings)
    session_factory = create_session_factory(settings)
    ocr_processor = _build_ocr_processor(settings)
    if not ocr_processor:
        return {"rerun_candidates": 0, "ocr_enriched": 0, "ocr_failed": 0}

    stats = {"rerun_candidates": 0, "ocr_enriched": 0, "ocr_failed": 0}
    with session_factory() as session:
        session: Session
        rows = (
            session.query(RawPost)
            .filter(RawPost.image_urls.is_not(None))
            .filter((RawPost.raw_image_text.is_(None)) | (RawPost.raw_image_text == ""))
            .order_by(RawPost.id.asc())
            .all()
        )
        stats["rerun_candidates"] = len(rows)

        for raw_post in rows:
            image_urls = _coerce_image_urls(raw_post.image_urls)
            if not image_urls:
                continue
            source_text = raw_post.raw_source_text or raw_post.raw_text
            metadata_json = _coerce_metadata(raw_post.metadata_json)
            post = CollectedPost(
                platform=raw_post.platform,
                post_id=raw_post.post_id,
                url=raw_post.url,
                title=raw_post.title,
                author_name=raw_post.author_name,
                published_at=raw_post.published_at,
                raw_html=raw_post.raw_html,
                raw_source_text=source_text,
                raw_image_text=raw_post.raw_image_text,
                raw_text=raw_post.raw_text,
                image_urls=image_urls,
                metadata_json=metadata_json,
            )
            try:
                enriched_post = ocr_processor.enrich_post(post)
                raw_post.raw_source_text = enriched_post.raw_source_text
                raw_post.raw_image_text = enriched_post.raw_image_text
                raw_post.raw_text = enriched_post.raw_text
                raw_post.image_urls = enriched_post.image_urls
                raw_post.metadata_json = enriched_post.metadata_json
                session.commit()
                if enriched_post.raw_image_text:
                    stats["ocr_enriched"] += 1
            except Exception:
                session.rollback()
                stats["ocr_failed"] += 1

    return stats


def export_obsidian_vault(settings: Settings) -> dict:
    create_tables(settings)
    session_factory = create_session_factory(settings)
    exporter = MarkdownExporter(settings)
    with session_factory() as session:
        return exporter.export_indexes(session)


def build_question_index(settings: Settings) -> dict:
    create_tables(settings)
    session_factory = create_session_factory(settings)
    analyzer = LLMAnalyzer(settings)
    builder = QuestionIndexBuilder(analyzer)
    with session_factory() as session:
        return builder.rebuild(session)


def list_taxonomy_suggestions(settings: Settings) -> list[dict]:
    create_tables(settings)
    session_factory = create_session_factory(settings)
    with session_factory() as session:
        rows = (
            session.query(TaxonomySuggestion)
            .order_by(TaxonomySuggestion.frequency.desc(), TaxonomySuggestion.suggested_name.asc())
            .all()
        )
        return [
            {
                "id": row.id,
                "suggested_name": row.suggested_name,
                "status": row.status,
                "frequency": row.frequency,
                "source_raw_post_ids": row.source_raw_post_ids or [],
                "example_questions": row.example_questions or [],
            }
            for row in rows
        ]


def import_manual_note(
    settings: Settings,
    *,
    title: str | None,
    text: str | None,
    text_file: Path | None,
    image_files: list[Path] | None,
    source_url: str | None,
    author_name: str | None,
) -> dict:
    create_tables(settings)
    session_factory = create_session_factory(settings)
    ocr_processor = _build_ocr_processor(settings)

    request = ManualImportRequest(
        title=title,
        text=text,
        text_file=text_file,
        image_files=image_files or [],
        source_url=source_url,
        author_name=author_name,
    )
    post = build_manual_post(settings, request)
    stats = {"collected": 1, "ocr_enriched": 0, "new_posts": 0, "processed": 0, "exported": 0, "analysis_failed": 0}

    with session_factory() as session:
        session: Session
        if ocr_processor:
            enriched_post = ocr_processor.enrich_post(post)
            if enriched_post.raw_image_text:
                stats["ocr_enriched"] += 1
            post = enriched_post
        raw_post, is_new = upsert_raw_post(session, post)
        stats["new_posts"] = 1 if is_new else 0
        if not raw_post.raw_text:
            raw_post.status = "detail_failed"
        session.commit()
        raw_post_id = raw_post.id

    analysis_stats = analyze_raw_posts(settings, raw_post_ids=[raw_post_id])
    for key, value in analysis_stats.items():
        stats[key] = stats.get(key, 0) + value

    if stats.get("processed", 0) > 0:
        index_stats = build_question_index(settings)
        export_stats = export_obsidian_vault(settings)
        stats["canonical_questions"] = index_stats.get("canonical_questions", 0)
        stats["knowledge_point_pages"] = export_stats.get("knowledge_point_pages", 0)
        stats["algorithm_pages"] = export_stats.get("algorithm_pages", 0)

    stats["raw_post_id"] = raw_post_id
    stats["post_id"] = post.post_id
    stats["platform"] = post.platform

    return stats


def _build_collector(settings: Settings):
    if settings.source_platform == "xiaohongshu_mcp":
        return XiaohongshuMCPCollector(settings)
    if settings.source_platform == "nowcoder":
        return NowcoderCollector(settings)
    raise ValueError(f"Unsupported SOURCE_PLATFORM: {settings.source_platform}")


def _build_ocr_processor(settings: Settings):
    if settings.ocr_enabled and settings.ocr_provider == "volcengine":
        return VolcengineOCRProcessor(settings)
    return None


def _coerce_image_urls(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return []
    return []


def _coerce_metadata(value):
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}
