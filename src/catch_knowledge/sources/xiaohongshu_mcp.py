from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set

import httpx

from catch_knowledge.config import Settings
from catch_knowledge.domain import CollectedPost

from .base import BaseCollector


@dataclass
class SearchFeed:
    feed_id: str
    xsec_token: str
    title: Optional[str]
    author_name: Optional[str]
    note_type: Optional[str]
    interact_info: Dict
    raw_item: Dict


class XiaohongshuMCPCollector(BaseCollector):
    platform = "xiaohongshu"

    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.Client(
            base_url=settings.xhs_mcp_base_url.rstrip("/"),
            timeout=60,
            follow_redirects=True,
        )

    def collect(self) -> List[CollectedPost]:
        self.ensure_logged_in()
        posts: List[CollectedPost] = []
        seen_ids: Set[str] = set()

        for keyword in self.settings.xhs_keywords:
            feeds = self._search_feeds(keyword)[: self.settings.xhs_max_results_per_keyword]
            for feed in feeds:
                if not feed.feed_id or not feed.xsec_token or feed.feed_id in seen_ids:
                    continue
                detail = self._get_feed_detail(feed, keyword)
                if detail is None:
                    continue
                posts.append(detail)
                seen_ids.add(feed.feed_id)

        return posts

    def ensure_logged_in(self) -> None:
        response = self.client.get("/api/v1/login/status")
        response.raise_for_status()
        payload = response.json()
        is_logged_in = bool(payload.get("data", {}).get("is_logged_in"))
        if not is_logged_in:
            raise RuntimeError(
                "xiaohongshu-mcp is not logged in. Start the MCP service, fetch a QR code, "
                "scan it in the Xiaohongshu app, and retry."
            )

    def save_login_qrcode(self) -> str:
        response = self.client.get("/api/v1/login/qrcode")
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data", {})
        img_data = data.get("img", "")
        if not img_data:
            raise RuntimeError("xiaohongshu-mcp did not return a QR code image.")
        base64_part = img_data.split(",", 1)[-1]
        binary = base64.b64decode(base64_part)
        self.settings.xhs_login_qrcode_path.write_bytes(binary)
        return str(self.settings.xhs_login_qrcode_path)

    def _search_feeds(self, keyword: str) -> List[SearchFeed]:
        response = self.client.post(
            "/api/v1/feeds/search",
            json={
                "keyword": keyword,
                "filters": {
                    "sort_by": self.settings.xhs_search_sort_by,
                    "note_type": self.settings.xhs_search_note_type,
                    "publish_time": self.settings.xhs_search_publish_time,
                    "search_scope": self.settings.xhs_search_scope,
                    "location": self.settings.xhs_search_location,
                },
            },
        )
        response.raise_for_status()
        payload = response.json()
        feeds = payload.get("data", {}).get("feeds", [])
        return [self._parse_search_feed(item) for item in feeds]

    def _get_feed_detail(self, feed: SearchFeed, keyword: str) -> Optional[CollectedPost]:
        response = self.client.post(
            "/api/v1/feeds/detail",
            json={
                "feed_id": feed.feed_id,
                "xsec_token": feed.xsec_token,
                "load_all_comments": self.settings.xhs_fetch_comments,
            },
        )
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data", {}).get("data", {})
        note = data.get("note") or {}
        if not note:
            return None

        comments = data.get("comments", {}).get("list", [])
        title = note.get("title") or feed.title
        desc = note.get("desc") or ""
        comment_text = self._flatten_comments(comments) if comments else ""
        combined_text = "\n\n".join(part for part in [title, desc, comment_text] if part)

        published_at = None
        timestamp = note.get("time")
        if isinstance(timestamp, (int, float)) and timestamp > 0:
            published_at = datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc)

        metadata = {
            "feed_id": feed.feed_id,
            "xsec_token": feed.xsec_token,
            "keyword_source": keyword,
            "note_type": note.get("type") or feed.note_type,
            "user": note.get("user") or {},
            "interact_info": note.get("interactInfo") or feed.interact_info,
            "comments_count": len(comments),
            "images": note.get("imageList") or [],
            "raw_detail": data,
        }

        return CollectedPost(
            platform=self.platform,
            post_id=feed.feed_id,
            url=f"https://www.xiaohongshu.com/explore/{feed.feed_id}?xsec_token={feed.xsec_token}",
            title=title,
            author_name=(note.get("user") or {}).get("nickname") or feed.author_name,
            published_at=published_at,
            raw_html=None,
            raw_text=combined_text,
            metadata_json=metadata,
        )

    @staticmethod
    def _parse_search_feed(item: dict) -> SearchFeed:
        note_card = item.get("noteCard") or {}
        user = note_card.get("user") or {}
        return SearchFeed(
            feed_id=item.get("id", ""),
            xsec_token=item.get("xsecToken", ""),
            title=note_card.get("displayTitle"),
            author_name=user.get("nickname") or user.get("nickName"),
            note_type=note_card.get("type"),
            interact_info=note_card.get("interactInfo") or {},
            raw_item=item,
        )

    @staticmethod
    def _flatten_comments(comments: List[Dict]) -> str:
        chunks: List[str] = []
        for comment in comments:
            content = comment.get("content")
            if content:
                chunks.append(content)
            for sub in comment.get("subComments") or []:
                sub_content = sub.get("content")
                if sub_content:
                    chunks.append(sub_content)
        if not chunks:
            return ""
        return "评论:\n" + "\n".join(f"- {item}" for item in chunks)
