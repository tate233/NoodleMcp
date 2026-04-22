from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from catch_knowledge.config import Settings
from catch_knowledge.db import create_session_factory, create_tables
from catch_knowledge.db.models import PostAnalysis, RawPost


@dataclass
class ParsedInterviewNote:
    path: Path
    raw_post_id: int
    company: Optional[str]
    role: Optional[str]
    direction: Optional[str]
    rounds: List[str]
    tags: List[str]
    source_url: Optional[str]
    title: Optional[str]
    interview_questions: List[str]
    question_points: List[str]
    summary: Optional[str]
    raw_source_text: Optional[str]
    raw_image_text: Optional[str]


def sync_obsidian_to_db(settings: Settings) -> dict:
    create_tables(settings)
    session_factory = create_session_factory(settings)
    paths = sorted((settings.knowledge_base_dir / "面经").glob("**/*.md"))
    stats = {"scanned": len(paths), "updated": 0, "skipped": 0, "errors": 0}

    with session_factory() as session:
        session: Session
        for path in paths:
            try:
                parsed = parse_interview_note(path)
                if parsed is None:
                    stats["skipped"] += 1
                    continue
                if _apply_note(session, parsed):
                    stats["updated"] += 1
                else:
                    stats["skipped"] += 1
            except Exception:
                stats["errors"] += 1
        session.commit()

    return stats


def parse_interview_note(path: Path) -> Optional[ParsedInterviewNote]:
    text = path.read_text(encoding="utf-8")
    frontmatter, body = _split_frontmatter(text)
    raw_post_id_text = frontmatter.get("raw_post_id")
    if not raw_post_id_text:
        return None

    try:
        raw_post_id = int(str(raw_post_id_text).strip())
    except ValueError:
        return None

    sections = _split_sections(body)
    return ParsedInterviewNote(
        path=path,
        raw_post_id=raw_post_id,
        company=_clean_scalar(frontmatter.get("company")),
        role=_clean_scalar(frontmatter.get("role")),
        direction=_clean_scalar(frontmatter.get("direction")),
        rounds=_coerce_list(frontmatter.get("rounds")),
        tags=[item for item in _coerce_list(frontmatter.get("tags")) if item != "面经"],
        source_url=_clean_scalar(frontmatter.get("source_url")),
        title=_extract_title(body),
        interview_questions=_parse_bullets(sections.get("面试题", "")),
        question_points=_parse_wiki_bullets(sections.get("知识点", "")),
        summary=_clean_text(sections.get("摘要")),
        raw_source_text=_clean_text(sections.get("原文")),
        raw_image_text=_clean_text(sections.get("图片 OCR")),
    )


def _apply_note(session: Session, parsed: ParsedInterviewNote) -> bool:
    raw_post = session.get(RawPost, parsed.raw_post_id)
    if raw_post is None or raw_post.analysis is None:
        return False

    analysis: PostAnalysis = raw_post.analysis
    if parsed.source_url:
        raw_post.url = parsed.source_url
    if parsed.title:
        raw_post.title = parsed.title
    raw_post.raw_source_text = parsed.raw_source_text
    raw_post.raw_image_text = parsed.raw_image_text
    raw_post.raw_text = _combine_raw_text(parsed.raw_source_text, parsed.raw_image_text)

    analysis.company = parsed.company
    analysis.job_role = parsed.role
    analysis.job_direction = parsed.direction
    analysis.interview_rounds = parsed.rounds
    analysis.tags = parsed.tags
    analysis.interview_questions = parsed.interview_questions
    analysis.question_points = parsed.question_points
    analysis.summary = parsed.summary
    analysis.normalized_json = _merge_normalized_json(analysis.normalized_json, parsed)
    raw_post.status = "manual_synced"
    return True


def _split_frontmatter(text: str) -> tuple[Dict[str, object], str]:
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    end_index = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            end_index = index
            break
    if end_index is None:
        return {}, text
    return _parse_simple_yaml(lines[1:end_index]), "\n".join(lines[end_index + 1 :])


def _parse_simple_yaml(lines: List[str]) -> Dict[str, object]:
    data: Dict[str, object] = {}
    current_key = None
    for line in lines:
        if not line.strip():
            continue
        if line.startswith("  - ") and current_key:
            data.setdefault(current_key, []).append(line[4:].strip())
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        current_key = key.strip()
        value = value.strip()
        data[current_key] = value if value else []
    return data


def _split_sections(body: str) -> Dict[str, str]:
    sections: Dict[str, List[str]] = {}
    current = None
    for line in body.splitlines():
        if line.startswith("## "):
            current = line[3:].strip()
            sections[current] = []
            continue
        if current:
            sections[current].append(line)
    return {key: "\n".join(lines).strip() for key, lines in sections.items()}


def _extract_title(body: str) -> Optional[str]:
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return None


def _parse_bullets(text: str) -> List[str]:
    items = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("- "):
            continue
        item = stripped[2:].strip()
        if item and not item.startswith("暂无"):
            items.append(item)
    return items


def _parse_wiki_bullets(text: str) -> List[str]:
    items = []
    for item in _parse_bullets(text):
        match = re.fullmatch(r"\[\[(.+?)(?:\|.+?)?\]\]", item)
        items.append(match.group(1).strip() if match else item)
    return items


def _coerce_list(value) -> List[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]


def _clean_scalar(value) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _clean_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    text = value.strip()
    return text or None


def _combine_raw_text(raw_source_text: Optional[str], raw_image_text: Optional[str]) -> Optional[str]:
    parts = []
    if raw_source_text:
        parts.append(raw_source_text.strip())
    if raw_image_text:
        parts.extend(["图片 OCR:", raw_image_text.strip()])
    return "\n\n".join(parts) if parts else None


def _merge_normalized_json(existing: Optional[Dict], parsed: ParsedInterviewNote) -> Dict:
    payload = dict(existing or {})
    payload.update(
        {
            "manual_synced": True,
            "company": parsed.company,
            "job_role": parsed.role,
            "job_direction": parsed.direction,
            "interview_rounds": parsed.rounds,
            "tags": parsed.tags,
            "interview_questions": parsed.interview_questions,
            "question_points": parsed.question_points,
            "summary": parsed.summary,
        }
    )
    return payload
