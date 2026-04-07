from __future__ import annotations

import argparse
import json
from pathlib import Path

from catch_knowledge.config import get_settings
from catch_knowledge.db import create_tables, migrate_sqlite_to_current_db
from catch_knowledge.llm import LLMAnalyzer
from catch_knowledge.pipeline import (
    analyze_raw_posts,
    build_question_index,
    export_obsidian_vault,
    reanalyze_fallback_posts,
    reanalyze_missing_questions,
    rerun_ocr_posts,
    run_pipeline,
)
from catch_knowledge.scheduler import run_scheduler
from catch_knowledge.sources.playwright_support import open_nowcoder_browser
from catch_knowledge.sources.xiaohongshu_mcp import XiaohongshuMCPCollector


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Catch Knowledge command line interface")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("run-once", help="Run the pipeline once")
    subparsers.add_parser("schedule", help="Run the daily scheduler")
    subparsers.add_parser("login-nowcoder", help="Open a browser and save Nowcoder login state")
    subparsers.add_parser("xhs-mcp-status", help="Check xiaohongshu-mcp login status")
    subparsers.add_parser("xhs-mcp-qrcode", help="Fetch and save the xiaohongshu-mcp login QR code")
    subparsers.add_parser("xhs-search", help="Preview Xiaohongshu search candidates without fetching details")
    subparsers.add_parser("analyze-pending", help="Analyze collected raw posts that have not been processed")
    subparsers.add_parser("reanalyze-fallback", help="Re-run LLM analysis for rows previously processed via fallback")
    subparsers.add_parser("reanalyze-missing-questions", help="Re-run LLM analysis for rows whose interview_questions column is still empty")
    subparsers.add_parser("rerun-ocr", help="Re-run OCR for rows with image URLs but empty raw_image_text")
    subparsers.add_parser("export-obsidian", help="Export current analysis results into an Obsidian-friendly vault")
    subparsers.add_parser("build-question-index", help="Build canonical question index with local per-topic merging")
    subparsers.add_parser("init-db", help="Initialize database tables for the current DATABASE_URL")
    migrate_parser = subparsers.add_parser("migrate-sqlite-to-db", help="Migrate data from a SQLite file into the current DATABASE_URL")
    migrate_parser.add_argument("--sqlite-path", default="./data/catch_knowledge.db", help="Path to the source SQLite file")
    subparsers.add_parser("llm-check", help="Check LLM connectivity using current .env settings")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    settings = get_settings()

    if args.command == "run-once":
        result = run_pipeline(settings)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command == "schedule":
        run_scheduler(settings)
        return

    if args.command == "login-nowcoder":
        with open_nowcoder_browser(settings) as context:
            page = context.new_page()
            page.goto("https://www.nowcoder.com/", wait_until="networkidle")
            page.wait_for_timeout(60000)
            page.close()
        print(
            json.dumps(
                {"saved_storage_state": str(settings.nowcoder_storage_state_path)},
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if args.command == "xhs-mcp-status":
        collector = XiaohongshuMCPCollector(settings)
        try:
            collector.ensure_logged_in()
            payload = {"is_logged_in": True, "base_url": settings.xhs_mcp_base_url}
        except Exception as exc:
            payload = {"is_logged_in": False, "base_url": settings.xhs_mcp_base_url, "error": str(exc)}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    if args.command == "xhs-mcp-qrcode":
        collector = XiaohongshuMCPCollector(settings)
        path = collector.save_login_qrcode()
        print(json.dumps({"qrcode_path": path}, ensure_ascii=False, indent=2))
        return

    if args.command == "xhs-search":
        collector = XiaohongshuMCPCollector(settings)
        previews = collector.preview_search_results()
        print(json.dumps(previews, ensure_ascii=False, indent=2))
        return

    if args.command == "analyze-pending":
        result = analyze_raw_posts(settings)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command == "reanalyze-fallback":
        result = reanalyze_fallback_posts(settings)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command == "reanalyze-missing-questions":
        result = reanalyze_missing_questions(settings)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command == "rerun-ocr":
        result = rerun_ocr_posts(settings)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command == "export-obsidian":
        result = export_obsidian_vault(settings)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command == "build-question-index":
        result = build_question_index(settings)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command == "init-db":
        create_tables(settings)
        print(json.dumps({"ok": True, "database_url": settings.database_url}, ensure_ascii=False, indent=2))
        return

    if args.command == "migrate-sqlite-to-db":
        result = migrate_sqlite_to_current_db(settings, Path(args.sqlite_path))
        print(
            json.dumps(
                {"ok": True, "database_url": settings.database_url, "sqlite_path": args.sqlite_path, "stats": result},
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if args.command == "llm-check":
        analyzer = LLMAnalyzer(settings)
        result = analyzer.check_connection()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    parser.error(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    main()
