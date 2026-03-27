from __future__ import annotations

from sqlalchemy.orm import Session

from catch_knowledge.config import Settings
from catch_knowledge.db import create_session_factory, create_tables
from catch_knowledge.exporters import MarkdownExporter
from catch_knowledge.llm import LLMAnalyzer
from catch_knowledge.sources import NowcoderCollector, XiaohongshuMCPCollector
from catch_knowledge.storage import save_analysis, save_kb_document, upsert_raw_post


def run_pipeline(settings: Settings) -> dict:
    create_tables(settings)
    session_factory = create_session_factory(settings)
    collector = _build_collector(settings)
    analyzer = LLMAnalyzer(settings)
    exporter = MarkdownExporter(settings)

    stats = {"collected": 0, "new_posts": 0, "processed": 0, "exported": 0}
    posts = collector.collect()
    stats["collected"] = len(posts)

    with session_factory() as session:
        session: Session
        for post in posts:
            raw_post, is_new = upsert_raw_post(session, post)
            if is_new:
                stats["new_posts"] += 1

            analysis_payload = analyzer.analyze(raw_post.title, raw_post.raw_text)
            analysis = save_analysis(session, raw_post, analysis_payload, settings.openai_model)
            stats["processed"] += 1

            if analysis.is_interview_experience:
                title, path = exporter.export(raw_post, analysis)
                save_kb_document(session, raw_post, title, path)
                stats["exported"] += 1

        session.commit()

    return stats


def _build_collector(settings: Settings):
    if settings.source_platform == "xiaohongshu_mcp":
        return XiaohongshuMCPCollector(settings)
    if settings.source_platform == "nowcoder":
        return NowcoderCollector(settings)
    raise ValueError(f"Unsupported SOURCE_PLATFORM: {settings.source_platform}")
