from __future__ import annotations

import re
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

from sqlalchemy.orm import Session

from catch_knowledge.config import Settings
from catch_knowledge.db.models import CanonicalQuestion, PostAnalysis, RawPost


class MarkdownExporter:
    def __init__(self, settings: Settings):
        self.settings = settings

    def export(self, raw_post: RawPost, analysis: PostAnalysis) -> Tuple[str, Path]:
        company = self._clean_name(analysis.company) or "未知公司"
        title = self._build_title(raw_post, analysis)
        directory = self.settings.knowledge_base_dir / "面经" / company
        directory.mkdir(parents=True, exist_ok=True)

        date_key = (raw_post.published_at or raw_post.crawled_at).strftime("%Y-%m-%d")
        filename = f"{date_key}_{self._slugify(title)}.md"
        path = directory / filename
        path.write_text(self._render_interview_note(raw_post, analysis, title), encoding="utf-8")
        return title, path

    def export_indexes(self, session: Session) -> dict:
        self._clear_generated_vault()
        rows = (
            session.query(RawPost, PostAnalysis)
            .join(PostAnalysis, PostAnalysis.raw_post_id == RawPost.id)
            .filter(PostAnalysis.is_interview_experience.is_(True))
            .order_by(RawPost.id.asc())
            .all()
        )

        stats = {"company_pages": 0, "knowledge_point_pages": 0, "algorithm_pages": 0, "index_pages": 0}
        for raw_post, analysis in rows:
            self.export(raw_post, analysis)
        self._export_company_pages(rows)
        stats["company_pages"] = len({self._clean_name(analysis.company) or "未知公司" for _, analysis in rows})

        canonical_count = session.query(CanonicalQuestion).count()
        if canonical_count:
            knowledge_points = self._export_canonical_knowledge_point_pages(session)
            algorithms = self._export_canonical_algorithm_pages(session)
        else:
            knowledge_points = self._export_knowledge_point_pages(rows)
            algorithms = self._export_algorithm_pages(rows)
        stats["knowledge_point_pages"] = len(knowledge_points)
        stats["algorithm_pages"] = len(algorithms)

        self._export_home_index(rows, knowledge_points, algorithms)
        stats["index_pages"] = 1
        return stats

    def _clear_generated_vault(self) -> None:
        base = self.settings.knowledge_base_dir.resolve()
        base.mkdir(parents=True, exist_ok=True)
        generated_dirs = ["面经", "公司", "面试题", "算法题"]
        for dirname in generated_dirs:
            path = (base / dirname).resolve()
            if path == base or base not in path.parents:
                raise ValueError(f"Refusing to clear unsafe path: {path}")
            if path.exists():
                shutil.rmtree(path)

        home = (base / "面经知识库.md").resolve()
        if home.exists():
            if base not in home.parents:
                raise ValueError(f"Refusing to remove unsafe path: {home}")
            home.unlink()

    def _render_interview_note(self, raw_post: RawPost, analysis: PostAnalysis, title: str) -> str:
        company = self._clean_name(analysis.company) or "未知公司"
        role = analysis.job_role or ""
        direction = analysis.job_direction or ""
        rounds = analysis.interview_rounds or []
        tags = analysis.tags or []
        question_points = analysis.question_points or []
        questions = analysis.interview_questions or []
        algorithms = self._extract_algorithm_questions(questions)

        frontmatter = [
            "---",
            f"platform: {raw_post.platform}",
            f"company: {company}",
            f"role: {role}",
            f"direction: {direction}",
            "rounds:",
            *[f"  - {item}" for item in rounds],
            "tags:",
            "  - 面经",
            *[f"  - {item}" for item in tags],
            f"source_url: {raw_post.url}",
            f"created: {(raw_post.published_at or raw_post.crawled_at).date().isoformat()}",
            "---",
            "",
        ]

        lines = [
            *frontmatter,
            f"# {title}",
            "",
            f"- 公司：[[{company}]]",
            f"- 岗位：{role}",
            f"- 方向：{direction}",
            f"- 轮次：{', '.join(rounds)}",
            f"- 原帖：{raw_post.url}",
            "",
            "## 面试题",
        ]
        if questions:
            lines.extend([f"- {item}" for item in questions])
        else:
            lines.append("- 暂无明确题目")

        lines.extend(["", "## 知识点"])
        if question_points:
            lines.extend([f"- [[{self._clean_name(item)}]]" for item in question_points])
        else:
            lines.append("- 暂无明确知识点")

        lines.extend(["", "## 算法题"])
        if algorithms:
            lines.extend([f"- {item}" for item in algorithms])
        else:
            lines.append("- 暂无明确算法题")

        lines.extend(
            [
                "",
                "## 摘要",
                analysis.summary or "",
                "",
                "## 原文",
                raw_post.raw_source_text or raw_post.raw_text or "",
                "",
                "## 图片 OCR",
                raw_post.raw_image_text or "",
                "",
            ]
        )
        return "\n".join(lines)

    def _export_company_pages(self, rows: Iterable[Tuple[RawPost, PostAnalysis]]) -> None:
        grouped: Dict[str, List[Tuple[RawPost, PostAnalysis]]] = defaultdict(list)
        for raw_post, analysis in rows:
            grouped[self._clean_name(analysis.company) or "未知公司"].append((raw_post, analysis))

        base = self.settings.knowledge_base_dir / "公司"
        base.mkdir(parents=True, exist_ok=True)
        for company, items in grouped.items():
            lines = [f"# {company}", "", "## 面经记录"]
            for raw_post, analysis in items:
                note_title = self._build_title(raw_post, analysis)
                rel_link = self._obsidian_link("面经", company, note_title, raw_post, analysis)
                lines.append(f"- {rel_link}")
            lines.append("")
            (base / f"{self._slugify(company)}.md").write_text("\n".join(lines), encoding="utf-8")

    def _export_knowledge_point_pages(self, rows: Iterable[Tuple[RawPost, PostAnalysis]]) -> Dict[str, int]:
        grouped: Dict[str, List[Tuple[str, RawPost, PostAnalysis]]] = defaultdict(list)
        for raw_post, analysis in rows:
            for question in analysis.interview_questions or []:
                points = analysis.question_points or ["未分类"]
                for point in points:
                    grouped[self._clean_name(point) or "未分类"].append((question, raw_post, analysis))

        base = self.settings.knowledge_base_dir / "面试题"
        base.mkdir(parents=True, exist_ok=True)
        for point, items in grouped.items():
            lines = [f"# {point}", "", f"出现次数：{len(items)}", "", "## 相关题目"]
            for question, raw_post, analysis in items:
                company = self._clean_name(analysis.company) or "未知公司"
                note_title = self._build_title(raw_post, analysis)
                lines.append(f"- {question}  来源：[[{company}]] / {self._obsidian_link('面经', company, note_title, raw_post, analysis)}")
            lines.append("")
            (base / f"{self._slugify(point)}.md").write_text("\n".join(lines), encoding="utf-8")

        return {point: len(items) for point, items in grouped.items()}

    def _export_algorithm_pages(self, rows: Iterable[Tuple[RawPost, PostAnalysis]]) -> Dict[str, int]:
        grouped: Dict[str, List[Tuple[RawPost, PostAnalysis]]] = defaultdict(list)
        for raw_post, analysis in rows:
            for question in self._extract_algorithm_questions(analysis.interview_questions or []):
                grouped[question].append((raw_post, analysis))

        base = self.settings.knowledge_base_dir / "算法题"
        base.mkdir(parents=True, exist_ok=True)
        for question, items in grouped.items():
            lines = [f"# {question}", "", f"出现次数：{len(items)}", "", "## 出现记录"]
            for raw_post, analysis in items:
                company = self._clean_name(analysis.company) or "未知公司"
                note_title = self._build_title(raw_post, analysis)
                lines.append(f"- [[{company}]] / {self._obsidian_link('面经', company, note_title, raw_post, analysis)}")
            lines.append("")
            (base / f"{self._slugify(question)}.md").write_text("\n".join(lines), encoding="utf-8")

        return {question: len(items) for question, items in grouped.items()}

    def _export_canonical_knowledge_point_pages(self, session: Session) -> Dict[str, int]:
        grouped: Dict[str, List[CanonicalQuestion]] = defaultdict(list)
        rows = (
            session.query(CanonicalQuestion)
            .filter(CanonicalQuestion.kind == "interview")
            .order_by(CanonicalQuestion.knowledge_point.asc(), CanonicalQuestion.frequency.desc())
            .all()
        )
        for row in rows:
            grouped[row.knowledge_point].append(row)

        base = self.settings.knowledge_base_dir / "面试题"
        base.mkdir(parents=True, exist_ok=True)
        for point, items in grouped.items():
            lines = [f"# {point}", "", f"题目数：{len(items)}", "", "## 题目"]
            for item in items:
                lines.append(f"- {item.canonical_text}  频次：{item.frequency}")
                subtopics = self._canonical_subtopics(item)
                if subtopics:
                    lines.append(f"  - 细考点：{', '.join(subtopics)}")
                for source in self._source_links(session, item.source_raw_post_ids or []):
                    lines.append(f"  - 来源：{source}")
            lines.append("")
            (base / f"{self._slugify(point)}.md").write_text("\n".join(lines), encoding="utf-8")

        return {point: sum(item.frequency for item in items) for point, items in grouped.items()}

    def _export_canonical_algorithm_pages(self, session: Session) -> Dict[str, int]:
        rows = (
            session.query(CanonicalQuestion)
            .filter(CanonicalQuestion.kind == "algorithm")
            .order_by(CanonicalQuestion.frequency.desc(), CanonicalQuestion.id.asc())
            .all()
        )
        base = self.settings.knowledge_base_dir / "算法题"
        base.mkdir(parents=True, exist_ok=True)
        for item in rows:
            lines = [f"# {item.canonical_text}", "", f"出现次数：{item.frequency}", "", "## 出现记录"]
            for source in self._source_links(session, item.source_raw_post_ids or []):
                lines.append(f"- {source}")
            lines.append("")
            (base / f"{self._slugify(item.canonical_text)}.md").write_text("\n".join(lines), encoding="utf-8")

        return {item.canonical_text: item.frequency for item in rows}

    def _export_home_index(self, rows, knowledge_points: Dict[str, int], algorithms: Dict[str, int]) -> None:
        base = self.settings.knowledge_base_dir
        lines = [
            "# 面经知识库",
            "",
            f"- 面经数量：{len(rows)}",
            f"- 知识点数量：{len(knowledge_points)}",
            f"- 算法题数量：{len(algorithms)}",
            "",
            "## 高频知识点",
        ]
        for point, count in sorted(knowledge_points.items(), key=lambda item: item[1], reverse=True)[:30]:
            lines.append(f"- [[{point}]]：{count}")

        lines.extend(["", "## 高频算法题"])
        for question, count in sorted(algorithms.items(), key=lambda item: item[1], reverse=True)[:30]:
            lines.append(f"- [[{question}]]：{count}")

        lines.append("")
        (base / "面经知识库.md").write_text("\n".join(lines), encoding="utf-8")

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
    def _clean_name(value) -> str:
        return str(value or "").strip()

    def _obsidian_link(self, category: str, company: str, title: str, raw_post: RawPost, analysis: PostAnalysis) -> str:
        date_key = (raw_post.published_at or raw_post.crawled_at).strftime("%Y-%m-%d")
        filename = f"{date_key}_{self._slugify(title)}"
        return f"[[{category}/{company}/{filename}|{title}]]"

    def _source_links(self, session: Session, raw_post_ids: List[int]) -> List[str]:
        if not raw_post_ids:
            return []
        rows = (
            session.query(RawPost, PostAnalysis)
            .join(PostAnalysis, PostAnalysis.raw_post_id == RawPost.id)
            .filter(RawPost.id.in_(raw_post_ids))
            .all()
        )
        links = []
        for raw_post, analysis in rows:
            company = self._clean_name(analysis.company) or "未知公司"
            title = self._build_title(raw_post, analysis)
            links.append(f"[[{company}]] / {self._obsidian_link('面经', company, title, raw_post, analysis)}")
        return links

    @staticmethod
    def _canonical_subtopics(item: CanonicalQuestion) -> List[str]:
        subtopics = []
        for variant in item.variants or []:
            if not isinstance(variant, dict):
                continue
            for subtopic in variant.get("subtopics") or []:
                text = str(subtopic).strip()
                if text:
                    subtopics.append(text)
        return sorted(set(subtopics))

    @staticmethod
    def _extract_algorithm_questions(questions: List[str]) -> List[str]:
        keywords = ["算法", "手撕", "leetcode", "hot100", "数组", "链表", "二叉树", "动态规划", "子数组", "LRU", "排序", "栈", "队列"]
        results = []
        for question in questions:
            text = str(question)
            lowered = text.lower()
            if any(keyword.lower() in lowered for keyword in keywords):
                results.append(text)
        return results
