from __future__ import annotations

import argparse
import json

from catch_knowledge.config import get_settings
from catch_knowledge.pipeline import run_pipeline
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

    parser.error(f"Unsupported command: {args.command}")
