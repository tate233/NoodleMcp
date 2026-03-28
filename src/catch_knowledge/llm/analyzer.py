from __future__ import annotations

import json
import time
from typing import Optional

from openai import OpenAI

from catch_knowledge.config import Settings
from catch_knowledge.domain import StructuredAnalysis

from .schemas import AnalysisSchema

SYSTEM_PROMPT = """你需要从中文论坛帖子中提取结构化的面经信息。
只返回 JSON，对象中必须包含以下字段：
is_interview_experience, company, job_role, job_direction, interview_rounds, tags, interview_questions, question_points, summary, difficulty

要求：
1. 如果帖子明显不是面经，is_interview_experience 设为 false。
2. interview_questions 只提取原文里明确出现的面试题，尽量保留原句，不要脑补，不要扩写，不要根据主题推测题目。
3. 如果原文没有明确题目，interview_questions 必须返回 []。
4. question_points 用于提炼更高层的考点或主题，可以比 interview_questions 更概括，但仍然必须基于原文。
5. interview_rounds 必须返回数组，例如 ["一面"]、["二面", "HR面"]；不要返回单个字符串。
6. tags 必须返回数组。
7. summary 用 1 到 2 句话概括帖子内容。
8. difficulty 使用 easy、medium、hard，或简短中文描述。
9. 不要输出 markdown 代码块，不要输出解释，只返回 JSON。
"""


class LLMAnalyzer:
    def __init__(self, settings: Settings):
        self.settings = settings

    def analyze(self, title: Optional[str], raw_text: Optional[str]) -> StructuredAnalysis:
        if not (self.settings.openai_api_key or "").strip():
            return self._fallback_analysis(title=title, raw_text=raw_text)

        content = self._build_prompt(title, raw_text)
        last_error = None
        for attempt in range(self.settings.llm_retry_count + 1):
            try:
                client = self._build_client()
                response = client.chat.completions.create(
                    model=self.settings.openai_model,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": content},
                    ],
                )
                payload = self._extract_json_text(self._extract_output_text(response))
                parsed = AnalysisSchema.model_validate(self._normalize_payload(json.loads(payload)))
                return StructuredAnalysis(
                    is_interview_experience=parsed.is_interview_experience,
                    company=parsed.company,
                    job_role=parsed.job_role,
                    job_direction=parsed.job_direction,
                    interview_rounds=parsed.interview_rounds,
                    tags=parsed.tags,
                    interview_questions=parsed.interview_questions,
                    question_points=parsed.question_points,
                    summary=parsed.summary,
                    difficulty=parsed.difficulty,
                    normalized_json=parsed.model_dump(),
                )
            except Exception as exc:
                last_error = exc
                if attempt >= self.settings.llm_retry_count:
                    break
                time.sleep(self.settings.llm_retry_backoff_seconds * (attempt + 1))

        fallback = self._fallback_analysis(title=title, raw_text=raw_text)
        fallback.normalized_json["llm_error"] = repr(last_error) if last_error else "unknown"
        fallback.normalized_json["llm_fallback"] = True
        return fallback

    def check_connection(self) -> dict:
        if not (self.settings.openai_api_key or "").strip():
            return {
                "ok": False,
                "base_url": self.settings.openai_base_url,
                "model": self.settings.openai_model,
                "error": "OPENAI_API_KEY is missing",
            }

        try:
            client = self._build_client()
            response = client.chat.completions.create(
                model=self.settings.openai_model,
                messages=[{"role": "user", "content": "reply with OK"}],
                max_tokens=5,
            )
            output = self._extract_output_text(response)
            return {
                "ok": True,
                "base_url": self.settings.openai_base_url,
                "model": self.settings.openai_model,
                "response": output,
            }
        except Exception as exc:
            return {
                "ok": False,
                "base_url": self.settings.openai_base_url,
                "model": self.settings.openai_model,
                "error": repr(exc),
            }

    def _build_client(self) -> OpenAI:
        return OpenAI(
            api_key=(self.settings.openai_api_key or "").strip() or None,
            base_url=self.settings.openai_base_url or None,
            timeout=180.0,
            max_retries=0,
        )

    @staticmethod
    def _build_prompt(title: Optional[str], raw_text: Optional[str]) -> str:
        return f"标题: {title or ''}\n\n正文:\n{raw_text or ''}"

    @staticmethod
    def _extract_output_text(response) -> str:
        return response.choices[0].message.content or "{}"

    @staticmethod
    def _extract_json_text(text: str) -> str:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            cleaned = "\n".join(lines).strip()

        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end >= start:
            return cleaned[start : end + 1]
        return cleaned

    @staticmethod
    def _normalize_payload(payload: dict) -> dict:
        normalized = dict(payload)
        for field in ["interview_rounds", "tags", "interview_questions", "question_points"]:
            normalized[field] = LLMAnalyzer._coerce_list(normalized.get(field))
        return normalized

    @staticmethod
    def _coerce_list(value):
        if value is None or value == "":
            return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return []
            separators = ["\n", "；", ";", "，", ",", "、", "|", "/"]
            items = [text]
            for separator in separators:
                if separator in text:
                    items = [part.strip() for part in text.split(separator) if part.strip()]
                    break
            return items
        return [str(value).strip()]

    @staticmethod
    def _fallback_analysis(title: Optional[str], raw_text: Optional[str]) -> StructuredAnalysis:
        text = "\n".join(part for part in [title, raw_text] if part)
        keywords = ["面经", "一面", "二面", "三面", "hr面", "OC", "oc"]
        is_interview = any(keyword in text for keyword in keywords)
        summary = (raw_text or "")[:200] if raw_text else None
        return StructuredAnalysis(
            is_interview_experience=is_interview,
            company=None,
            job_role=None,
            job_direction=None,
            interview_rounds=[],
            tags=[],
            interview_questions=[],
            question_points=[],
            summary=summary,
            difficulty=None,
            normalized_json={
                "fallback": True,
                "matched_keywords": [keyword for keyword in keywords if keyword in text],
            },
        )
