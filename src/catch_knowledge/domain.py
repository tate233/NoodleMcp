from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional


@dataclass
class CollectedPost:
    platform: str
    post_id: str
    url: str
    title: Optional[str]
    author_name: Optional[str]
    published_at: Optional[datetime]
    raw_html: Optional[str]
    raw_source_text: Optional[str]
    raw_image_text: Optional[str]
    raw_text: Optional[str]
    image_urls: List[str] = field(default_factory=list)
    metadata_json: Dict = field(default_factory=dict)


@dataclass
class StructuredAnalysis:
    content_type: str
    is_interview_experience: bool
    company: Optional[str]
    job_role: Optional[str]
    job_direction: Optional[str]
    interview_rounds: List[str]
    tags: List[str]
    interview_questions: List[str]
    question_points: List[str]
    summary: Optional[str]
    difficulty: Optional[str]
    normalized_json: Dict
