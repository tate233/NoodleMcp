from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

from sqlalchemy import String, cast
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
from catch_knowledge.storage import save_analysis, upsert_raw_post


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

    if analysis_stats.get("processed", 0) > 0:
        sync_stats = sync_incremental_outputs(settings, raw_post_ids)
        stats["canonical_questions"] = sync_stats.get("canonical_questions", 0)
        stats["knowledge_point_pages"] = sync_stats.get("knowledge_point_pages", 0)
        stats["algorithm_pages"] = sync_stats.get("algorithm_pages", 0)
        stats["company_pages"] = sync_stats.get("company_pages", 0)
        stats["exported"] = sync_stats.get("exported", stats.get("exported", 0))

    return stats


def analyze_raw_posts(settings: Settings, raw_post_ids=None) -> dict:
    create_tables(settings)
    session_factory = create_session_factory(settings)
    analyzer = LLMAnalyzer(settings)
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
                _update_retry_queue_metadata(session, raw_post, analysis, settings)
                stats["processed"] += 1

                session.commit()
                time.sleep(1)
            except Exception as exc:
                metadata = _coerce_metadata(raw_post.metadata_json)
                metadata["analysis_error"] = repr(exc)
                raw_post.metadata_json = metadata
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
                | (cast(PostAnalysis.normalized_json, String).like('%"llm_fallback": true%'))
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
                | (cast(PostAnalysis.interview_questions, String) == "[]")
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


def sync_incremental_outputs(settings: Settings, raw_post_ids: list[int]) -> dict:
    create_tables(settings)
    session_factory = create_session_factory(settings)
    analyzer = LLMAnalyzer(settings)
    builder = QuestionIndexBuilder(analyzer)
    exporter = MarkdownExporter(settings)

    with session_factory() as session:
        session: Session
        index_stats = builder.sync_posts(session, raw_post_ids)
        export_stats = exporter.sync_posts(session, raw_post_ids)
        session.commit()
        return {
            "canonical_questions": index_stats.get("canonical_questions", 0),
            "knowledge_point_pages": export_stats.get("knowledge_point_pages", 0),
            "algorithm_pages": export_stats.get("algorithm_pages", 0),
            "company_pages": export_stats.get("company_pages", 0),
            "exported": export_stats.get("note_pages", 0),
        }


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

    with session_factory() as session:
        session: Session
        current_raw_post = session.get(RawPost, raw_post_id)
        current_status = current_raw_post.status if current_raw_post else None

    if current_status == "analysis_fallback":
        retry_result = reanalyze_single_post(settings, raw_post_id)
        stats["processed"] = max(stats.get("processed", 0), 1 if retry_result.get("processed") else 0)
        stats["analysis_failed"] += retry_result.get("analysis_failed", 0)

    if stats.get("processed", 0) > 0:
        sync_stats = sync_incremental_outputs(settings, [raw_post_id])
        stats["canonical_questions"] = sync_stats.get("canonical_questions", 0)
        stats["knowledge_point_pages"] = sync_stats.get("knowledge_point_pages", 0)
        stats["algorithm_pages"] = sync_stats.get("algorithm_pages", 0)
        stats["company_pages"] = sync_stats.get("company_pages", 0)
        stats["exported"] = sync_stats.get("exported", 0)

    stats["raw_post_id"] = raw_post_id
    stats["post_id"] = post.post_id
    stats["platform"] = post.platform

    return stats


def reanalyze_single_post(settings: Settings, raw_post_id: int) -> dict:
    create_tables(settings)
    session_factory = create_session_factory(settings)
    analyzer = LLMAnalyzer(settings)
    with session_factory() as session:
        session: Session
        raw_post = session.get(RawPost, raw_post_id)
        if raw_post is None:
            raise ValueError("raw post not found")
        if not raw_post.raw_text:
            raise ValueError("raw post has no analyzable text")

        analysis_payload = None
        for _ in range(max(1, settings.llm_retry_count + 2)):
            candidate = analyzer.analyze(raw_post.title, raw_post.raw_text)
            analysis_payload = candidate
            if not (candidate.normalized_json or {}).get("llm_fallback"):
                break

        analysis = save_analysis(session, raw_post, analysis_payload, settings.openai_model)
        _update_retry_queue_metadata(session, raw_post, analysis, settings)
        session.commit()
        result = {
            "processed": 1 if raw_post.status != "analysis_failed" else 0,
            "analysis_failed": 1 if raw_post.status == "analysis_failed" else 0,
            "exported": 0,
            "status": raw_post.status,
        }

    if result["processed"]:
        sync_stats = sync_incremental_outputs(settings, [raw_post_id])
        result["exported"] = sync_stats.get("exported", 0)
    return result


def process_llm_retry_queue(settings: Settings, limit: int = 20) -> dict:
    create_tables(settings)
    session_factory = create_session_factory(settings)
    now = datetime.now(ZoneInfo(settings.timezone))
    processed = 0
    resolved = 0
    still_queued = 0
    failed = 0

    with session_factory() as session:
        session: Session
        rows = session.query(RawPost).order_by(RawPost.id.asc()).all()
        queued_ids = []
        for row in rows:
            metadata = _coerce_metadata(row.metadata_json)
            retry_queue = metadata.get("llm_retry_queue") or {}
            if retry_queue.get("status") != "pending":
                continue
            next_retry_at = retry_queue.get("next_retry_at")
            if next_retry_at:
                try:
                    retry_dt = datetime.fromisoformat(next_retry_at)
                    if retry_dt.tzinfo is None:
                        retry_dt = retry_dt.replace(tzinfo=ZoneInfo(settings.timezone))
                    if retry_dt > now:
                        continue
                except Exception:
                    pass
            queued_ids.append(row.id)
            if len(queued_ids) >= limit:
                break

    for raw_post_id in queued_ids:
        processed += 1
        try:
            result = reanalyze_single_post(settings, raw_post_id)
            if result.get("status") == "analysis_fallback":
                still_queued += 1
            elif result.get("status") == "analysis_failed":
                failed += 1
            else:
                resolved += 1
        except Exception:
            failed += 1

    return {
        "processed": processed,
        "resolved": resolved,
        "still_queued": still_queued,
        "failed": failed,
    }


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


def _update_retry_queue_metadata(session: Session, raw_post: RawPost, analysis: PostAnalysis, settings: Settings) -> None:
    metadata = _coerce_metadata(raw_post.metadata_json)
    queue = dict(metadata.get("llm_retry_queue") or {})
    is_fallback = bool((analysis.normalized_json or {}).get("llm_fallback"))
    now = datetime.now(ZoneInfo(settings.timezone))

    if not is_fallback:
        if "llm_retry_queue" in metadata:
            metadata.pop("llm_retry_queue", None)
        raw_post.metadata_json = metadata
        return

    attempts = int(queue.get("attempts") or 0) + 1
    if attempts >= settings.llm_queue_max_attempts:
        queue_status = "exhausted"
    else:
        queue_status = "pending"

    queue.update(
        {
            "status": queue_status,
            "attempts": attempts,
            "last_error": (analysis.normalized_json or {}).get("llm_error"),
            "last_fallback_at": now.isoformat(),
            "next_retry_at": (now + timedelta(seconds=settings.llm_queue_retry_delay_seconds)).isoformat(),
        }
    )
    metadata["llm_retry_queue"] = queue
    raw_post.metadata_json = metadata
