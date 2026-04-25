from __future__ import annotations

import argparse
import json
from pathlib import Path

from catch_knowledge.config import get_settings
from catch_knowledge.db import create_tables, migrate_sqlite_to_current_db
from catch_knowledge.llm import LLMAnalyzer
from catch_knowledge.obsidian_sync import sync_obsidian_to_db
from catch_knowledge.pipeline import (
    analyze_raw_posts,
    build_question_index,
    export_obsidian_vault,
    import_manual_note,
    list_taxonomy_suggestions,
    process_llm_retry_queue,
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
    retry_queue_parser = subparsers.add_parser("process-llm-retry-queue", help="Retry queued rows that previously fell back due to LLM instability")
    retry_queue_parser.add_argument("--limit", type=int, default=20, help="Maximum queued rows to process")
    subparsers.add_parser("rerun-ocr", help="Re-run OCR for rows with image URLs but empty raw_image_text")
    manual_parser = subparsers.add_parser("manual-import", help="Import a manual interview note from local text and/or image files")
    manual_parser.add_argument("--title", help="Optional title for the imported note")
    manual_parser.add_argument("--text", help="Inline text content for the interview note")
    manual_parser.add_argument("--text-file", help="Path to a local txt/md file")
    manual_parser.add_argument("--image", action="append", default=[], help="Path to a local image file. Repeat the flag for multiple images.")
    manual_parser.add_argument("--source-url", help="Optional source URL to record with the note")
    manual_parser.add_argument("--author", help="Optional author name")
    web_parser = subparsers.add_parser("web", help="Run the lightweight web console")
    web_parser.add_argument("--host", default="127.0.0.1", help="Host to bind the web server")
    web_parser.add_argument("--port", type=int, default=8000, help="Port to bind the web server")
    qq_parser = subparsers.add_parser("qq-adapter", help="Run the NapCat QQ adapter webhook")
    qq_parser.add_argument("--host", default="127.0.0.1", help="Host to bind the QQ adapter")
    qq_parser.add_argument("--port", type=int, default=8090, help="Port to bind the QQ adapter")
    qq_parser.add_argument(
        "--ingest-base-url",
        default="http://127.0.0.1:8000",
        help="Base URL of the catch-knowledge web service",
    )
    qq_parser.add_argument(
        "--napcat-api-base-url",
        default="http://127.0.0.1:3000",
        help="Base URL of the NapCat HTTP API",
    )
    qq_parser.add_argument(
        "--napcat-access-token",
        default="",
        help="Optional access token for calling NapCat API",
    )
    qq_parser.add_argument(
        "--webhook-secret",
        default="",
        help="Optional bearer token expected from NapCat webhook requests",
    )
    subparsers.add_parser("export-obsidian", help="Export current analysis results into an Obsidian-friendly vault")
    subparsers.add_parser("sync-obsidian", help="Sync edited interview notes from Obsidian back into the database")
    subparsers.add_parser("build-question-index", help="Build canonical question index with local per-topic merging")
    subparsers.add_parser("list-taxonomy-suggestions", help="List pending taxonomy extension suggestions collected during indexing")
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

    if args.command == "process-llm-retry-queue":
        result = process_llm_retry_queue(settings, limit=args.limit)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command == "rerun-ocr":
        result = rerun_ocr_posts(settings)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command == "manual-import":
        if not args.text and not args.text_file and not args.image:
            parser.error("manual-import requires at least one of --text, --text-file, or --image")
        result = import_manual_note(
            settings,
            title=args.title,
            text=args.text,
            text_file=Path(args.text_file) if args.text_file else None,
            image_files=[Path(item) for item in args.image],
            source_url=args.source_url,
            author_name=args.author,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command == "web":
        try:
            import uvicorn
        except ImportError as exc:
            raise SystemExit("Web UI dependencies are missing. Install with: pip install -e .[web]") from exc

        uvicorn.run("catch_knowledge.web.app:app", host=args.host, port=args.port, reload=False)
        return

    if args.command == "qq-adapter":
        try:
            import uvicorn
        except ImportError as exc:
            raise SystemExit("Web/API dependencies are missing. Install with: pip install -e .[web]") from exc

        from catch_knowledge.adapters import create_qq_adapter_app

        app = create_qq_adapter_app(
            ingest_base_url=args.ingest_base_url,
            napcat_api_base_url=args.napcat_api_base_url or None,
            napcat_access_token=args.napcat_access_token or None,
            webhook_secret=args.webhook_secret or None,
        )
        uvicorn.run(app, host=args.host, port=args.port, reload=False)
        return

    if args.command == "export-obsidian":
        result = export_obsidian_vault(settings)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command == "sync-obsidian":
        result = sync_obsidian_to_db(settings)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command == "build-question-index":
        result = build_question_index(settings)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command == "list-taxonomy-suggestions":
        result = list_taxonomy_suggestions(settings)
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
