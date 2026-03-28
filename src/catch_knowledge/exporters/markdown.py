from __future__ import annotations

import re
from pathlib import Path
from typing import Tuple

from catch_knowledge.config import Settings
from catch_knowledge.db.models import PostAnalysis, RawPost


class MarkdownExporter:
    def __init__(self, settings: Settings):
        self.settings = settings

    def export(self, raw_post: RawPost, analysis: PostAnalysis) -> Tuple[str, Path]:
        month_key = (raw_post.published_at or raw_post.crawled_at).strftime("%Y-%m")
        directory = self.settings.knowledge_base_dir / raw_post.platform / month_key
        directory.mkdir(parents=True, exist_ok=True)

        title = self._build_title(raw_post, analysis)
        filename = f"{self._slugify(title)}.md"
        path = directory / filename
        path.write_text(self._render_markdown(raw_post, analysis, title), encoding="utf-8")
        return title, path

    @staticmethod
    def _build_title(raw_post: RawPost, analysis: PostAnalysis) -> str:
        pieces = [
            analysis.company or "未知公司",
            analysis.job_role or "未知岗位",
            " ".join(analysis.interview_rounds or []) or raw_post.title or "面经",
        ]
        return " ".join(piece for piece in pieces if piece)

    @staticmethod
    def _slugify(value: str) -> str:
        value = re.sub(r"[^\w\u4e00-\u9fff\- ]+", "", value).strip()
        value = re.sub(r"\s+", "_", value)
        return value[:120] or "document"

    @staticmethod
    def _render_markdown(raw_post: RawPost, analysis: PostAnalysis, title: str) -> str:
        lines = [
            f"# {title}",
            "",
            f"- 平台：{raw_post.platform}",
            f"- 原帖链接：{raw_post.url}",
            f"- 发布时间：{raw_post.published_at.isoformat() if raw_post.published_at else ''}",
            f"- 作者：{raw_post.author_name or ''}",
            f"- 公司：{analysis.company or ''}",
            f"- 岗位：{analysis.job_role or ''}",
            f"- 方向：{analysis.job_direction or ''}",
            f"- 轮次：{', '.join(analysis.interview_rounds or [])}",
            f"- 标签：{', '.join(analysis.tags or [])}",
            "",
            "## 摘要",
            analysis.summary or "",
            "",
            "## 面试题",
        ]
        interview_questions = analysis.interview_questions or []
        question_points = analysis.question_points or []
        if interview_questions:
            lines.extend([f"- {item}" for item in interview_questions])
        else:
            lines.append("- 暂无抽取到具体题目")

        lines.extend(
            [
                "",
                "## 考点",
            ]
        )
        if question_points:
            lines.extend([f"- {item}" for item in question_points])
        else:
            lines.append("- 暂无结构化考点")
        lines.extend(
            [
                "",
                "## 原文",
                raw_post.raw_text or "",
                "",
            ]
        )
        return "\n".join(lines)
