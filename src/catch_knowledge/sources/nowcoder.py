from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from dateutil import parser as date_parser

from catch_knowledge.config import Settings
from catch_knowledge.domain import CollectedPost

from .base import BaseCollector
from .playwright_support import fetch_page_with_playwright, open_nowcoder_browser


class NowcoderCollector(BaseCollector):
    platform = "nowcoder"

    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.Client(
            timeout=settings.nowcoder_request_timeout_seconds,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
                )
            },
            follow_redirects=True,
        )

    def collect(self) -> List[CollectedPost]:
        posts = self._collect_via_http()
        if self.settings.nowcoder_use_playwright and not posts:
            posts = self._collect_via_playwright()
        unique_by_url: Dict[str, CollectedPost] = {post.url: post for post in posts}
        return list(unique_by_url.values())

    def _collect_via_http(self) -> List[CollectedPost]:
        posts: List[CollectedPost] = []
        for seed_url in self.settings.nowcoder_seed_urls:
            posts.extend(self._collect_from_seed(seed_url))
        return posts

    def _collect_via_playwright(self) -> List[CollectedPost]:
        posts: List[CollectedPost] = []
        with open_nowcoder_browser(self.settings) as context:
            for seed_url in self.settings.nowcoder_seed_urls:
                rendered = fetch_page_with_playwright(context, seed_url)
                soup = BeautifulSoup(rendered.html, "html.parser")
                candidates = self._extract_candidate_links(soup, seed_url)
                for url in candidates:
                    detail = self._fetch_post_detail_with_playwright(context, url)
                    if detail:
                        posts.append(detail)
        return posts

    def _collect_from_seed(self, seed_url: str) -> List[CollectedPost]:
        response = self.client.get(seed_url)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        candidates = self._extract_candidate_links(soup, seed_url)

        collected: List[CollectedPost] = []
        for url in candidates:
            detail = self._fetch_post_detail(url)
            if detail:
                collected.append(detail)
        return collected

    def _extract_candidate_links(self, soup: BeautifulSoup, base_url: str) -> List[str]:
        links: List[str] = []
        for anchor in soup.select("a[href]"):
            href = anchor.get("href", "")
            absolute = urljoin(base_url, href)
            parsed = urlparse(absolute)
            if parsed.netloc.endswith("nowcoder.com") and self._looks_like_post_path(parsed.path):
                links.append(absolute)
        return list(dict.fromkeys(links))

    def _fetch_post_detail(self, url: str) -> Optional[CollectedPost]:
        response = self.client.get(url)
        if response.status_code >= 400:
            return None
        soup = BeautifulSoup(response.text, "html.parser")

        title = self._extract_text(soup, ["h1", "title"])
        body = self._extract_body_text(soup)
        if not body:
            return None

        author = self._extract_text(soup, [".author-name", ".user-name", "[data-author-name]"])
        published_at = self._extract_datetime(soup)
        post_id = self._derive_post_id(url)

        return CollectedPost(
            platform=self.platform,
            post_id=post_id,
            url=url,
            title=title,
            author_name=author,
            published_at=published_at,
            raw_html=response.text,
            raw_text=body,
            metadata_json={"seeded": True},
        )

    def _fetch_post_detail_with_playwright(self, context, url: str) -> Optional[CollectedPost]:
        rendered = fetch_page_with_playwright(context, url)
        soup = BeautifulSoup(rendered.html, "html.parser")
        title = self._extract_text(soup, ["h1", "title"])
        body = self._extract_body_text(soup)
        if not body:
            return None

        author = self._extract_text(soup, [".author-name", ".user-name", "[data-author-name]"])
        published_at = self._extract_datetime(soup)
        post_id = self._derive_post_id(url)

        return CollectedPost(
            platform=self.platform,
            post_id=post_id,
            url=url,
            title=title,
            author_name=author,
            published_at=published_at,
            raw_html=rendered.html,
            raw_text=body,
            metadata_json={"seeded": True, "used_playwright": True},
        )

    @staticmethod
    def _extract_text(soup: BeautifulSoup, selectors: List[str]) -> Optional[str]:
        for selector in selectors:
            node = soup.select_one(selector)
            if node:
                text = node.get_text(" ", strip=True)
                if text:
                    return text
        return None

    @staticmethod
    def _extract_body_text(soup: BeautifulSoup) -> Optional[str]:
        body_selectors = [
            ".post-content",
            ".rich-text",
            ".content",
            "article",
            "main",
        ]
        for selector in body_selectors:
            node = soup.select_one(selector)
            if node:
                text = node.get_text("\n", strip=True)
                if len(text) > 80:
                    return text
        return None

    @staticmethod
    def _extract_datetime(soup: BeautifulSoup) -> Optional[datetime]:
        time_node = soup.select_one("time")
        if time_node:
            raw = time_node.get("datetime") or time_node.get_text(" ", strip=True)
            try:
                return date_parser.parse(raw)
            except (ValueError, TypeError, OverflowError):
                return None
        return None

    @staticmethod
    def _derive_post_id(url: str) -> str:
        path = urlparse(url).path.rstrip("/")
        return path.split("/")[-1]

    @staticmethod
    def _looks_like_post_path(path: str) -> bool:
        candidates = [
            "/discuss/",
            "/feed/main/detail/",
            "/interview/experience/",
        ]
        return any(candidate in path for candidate in candidates)
