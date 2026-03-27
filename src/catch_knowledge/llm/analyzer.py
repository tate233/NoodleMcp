from __future__ import annotations

import json
from typing import Optional

from openai import OpenAI

from catch_knowledge.config import Settings
from catch_knowledge.domain import StructuredAnalysis

from .schemas import AnalysisSchema

SYSTEM_PROMPT = """You extract structured interview-experience information from Chinese forum posts.
Return only JSON with these keys:
is_interview_experience, company, job_role, job_direction, interview_rounds, tags, question_points, summary, difficulty.
Use concise Chinese or English strings.
If the post is not clearly an interview experience, set is_interview_experience to false.
"""


class LLMAnalyzer:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = OpenAI(
            api_key=settings.openai_api_key or None,
            base_url=settings.openai_base_url or None,
        )

    def analyze(self, title: Optional[str], raw_text: Optional[str]) -> StructuredAnalysis:
        if not self.settings.openai_api_key:
            return self._fallback_analysis(title=title, raw_text=raw_text)

        content = self._build_prompt(title, raw_text)
        response = self.client.chat.completions.create(
            model=self.settings.openai_model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": content},
            ],
            response_format={"type": "json_object"},
        )
        payload = self._extract_output_text(response)
        parsed = AnalysisSchema.model_validate(json.loads(payload))
        return StructuredAnalysis(
            is_interview_experience=parsed.is_interview_experience,
            company=parsed.company,
            job_role=parsed.job_role,
            job_direction=parsed.job_direction,
            interview_rounds=parsed.interview_rounds,
            tags=parsed.tags,
            question_points=parsed.question_points,
            summary=parsed.summary,
            difficulty=parsed.difficulty,
            normalized_json=parsed.model_dump(),
        )

    @staticmethod
    def _build_prompt(title: Optional[str], raw_text: Optional[str]) -> str:
        return f"标题: {title or ''}\n\n正文:\n{raw_text or ''}"

    @staticmethod
    def _extract_output_text(response) -> str:
        return response.choices[0].message.content or "{}"

    @staticmethod
    def _fallback_analysis(title: Optional[str], raw_text: Optional[str]) -> StructuredAnalysis:
        text = "\n".join(part for part in [title, raw_text] if part)
        keywords = ["面经", "一面", "二面", "三面", "hr面", "oc"]
        is_interview = any(keyword in text for keyword in keywords)
        summary = (raw_text or "")[:200] if raw_text else None
        return StructuredAnalysis(
            is_interview_experience=is_interview,
            company=None,
            job_role=None,
            job_direction=None,
            interview_rounds=[],
            tags=[],
            question_points=[],
            summary=summary,
            difficulty=None,
            normalized_json={
                "fallback": True,
                "matched_keywords": [keyword for keyword in keywords if keyword in text],
            },
        )
