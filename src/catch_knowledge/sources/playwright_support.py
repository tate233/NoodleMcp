from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass

from catch_knowledge.config import Settings


@dataclass
class RenderedPage:
    url: str
    html: str


@contextmanager
def open_nowcoder_browser(settings: Settings):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is not installed. Run `pip install -e .[playwright]` and "
            "`playwright install chromium` first."
        ) from exc

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=settings.nowcoder_browser_headless)
        context_kwargs = {}
        if settings.nowcoder_storage_state_path.exists():
            context_kwargs["storage_state"] = str(settings.nowcoder_storage_state_path)
        context = browser.new_context(**context_kwargs)
        try:
            yield context
        finally:
            context.storage_state(path=str(settings.nowcoder_storage_state_path))
            context.close()
            browser.close()


def fetch_page_with_playwright(context, url: str, wait_seconds: int = 5) -> RenderedPage:
    page = context.new_page()
    try:
        page.goto(url, wait_until="networkidle")
        page.wait_for_timeout(wait_seconds * 1000)
        return RenderedPage(url=url, html=page.content())
    finally:
        page.close()
