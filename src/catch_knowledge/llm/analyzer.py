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
content_type, is_interview_experience, company, job_role, job_direction, interview_rounds, tags, interview_questions, question_points, summary, difficulty

要求：
1. content_type 只能是 interview_note、knowledge_snippet、algorithm_snippet、noise 四选一。
2. 如果是完整面经或较完整的面试记录，content_type 设为 interview_note。
3. 如果只是零散经验、几条注意点、少量题目总结，更适合直接归入知识点，不适合作为独立面经，content_type 设为 knowledge_snippet。
4. 如果内容主要是算法题/手撕题/题解，content_type 设为 algorithm_snippet。
5. 如果明显不是面经相关内容，content_type 设为 noise，同时 is_interview_experience 设为 false。
6. interview_questions 只提取原文里明确出现的面试题，尽量保留原句，不要脑补，不要扩写，不要根据主题推测题目。
7. 如果原文没有明确题目，interview_questions 必须返回 []。
8. question_points 用于提炼更高层的考点或主题，可以比 interview_questions 更概括，但仍然必须基于原文。
9. interview_rounds 必须返回数组，例如 ["一面"]、["二面", "HR面"]；不要返回单个字符串。
10. tags 必须返回数组。
11. summary 用 1 到 2 句话概括帖子内容。
12. difficulty 使用 easy、medium、hard，或简短中文描述。
13. 不要输出 markdown 代码块，不要输出解释，只返回 JSON。
"""


class LLMAnalyzer:
    def __init__(self, settings: Settings):
        self.settings = settings

    def analyze(self, title: Optional[str], raw_text: Optional[str]) -> StructuredAnalysis:
        if not (self.settings.openai_api_key or "").strip():
            return self._fallback_analysis(title=title, raw_text=raw_text)

        content = self._build_prompt(title, raw_text)
        last_error = None
        targets = self._build_llm_targets()
        for target in targets:
            for attempt in range(self.settings.llm_retry_count + 1):
                try:
                    client = self._build_client(api_key=target["api_key"], base_url=target["base_url"])
                    response = client.chat.completions.create(
                        model=target["model"],
                        messages=[
                            {"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": content},
                        ],
                    )
                    payload = self._extract_json_text(self._extract_output_text(response))
                    parsed = AnalysisSchema.model_validate(self._normalize_payload(json.loads(payload)))
                    normalized_payload = parsed.model_dump()
                    normalized_payload = self._apply_short_text_heuristics(
                        normalized_payload,
                        title=title,
                        raw_text=raw_text,
                    )
                    normalized_payload["llm_model_used"] = target["model"]
                    normalized_payload["llm_base_url_used"] = target["base_url"]
                    normalized_payload["llm_target_name"] = target["name"]
                    return StructuredAnalysis(
                        content_type=normalized_payload["content_type"],
                        is_interview_experience=normalized_payload["is_interview_experience"],
                        company=normalized_payload.get("company"),
                        job_role=normalized_payload.get("job_role"),
                        job_direction=normalized_payload.get("job_direction"),
                        interview_rounds=normalized_payload.get("interview_rounds", []),
                        tags=normalized_payload.get("tags", []),
                        interview_questions=normalized_payload.get("interview_questions", []),
                        question_points=normalized_payload.get("question_points", []),
                        summary=normalized_payload.get("summary"),
                        difficulty=normalized_payload.get("difficulty"),
                        normalized_json=normalized_payload,
                    )
                except Exception as exc:
                    last_error = exc
                    if attempt >= self.settings.llm_retry_count:
                        break
                    backoff = self.settings.llm_retry_backoff_seconds * (
                        self.settings.llm_retry_backoff_multiplier ** attempt
                    )
                    time.sleep(backoff)

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
            target = self._build_llm_targets()[0]
            client = self._build_client(api_key=target["api_key"], base_url=target["base_url"])
            response = client.chat.completions.create(
                model=target["model"],
                messages=[{"role": "user", "content": "reply with OK"}],
                max_tokens=5,
            )
            output = self._extract_output_text(response)
            return {
                "ok": True,
                "base_url": target["base_url"],
                "model": target["model"],
                "response": output,
            }
        except Exception as exc:
            return {
                "ok": False,
                "base_url": self.settings.openai_base_url,
                "model": self.settings.openai_model,
                "error": repr(exc),
            }

    def match_canonical_question(self, knowledge_point: str, new_question: str, candidates: list[dict]) -> int | None:
        if not candidates:
            return None
        trimmed_candidates = candidates[:40]
        prompt = {
            "knowledge_point": knowledge_point,
            "new_question": new_question,
            "candidates": trimmed_candidates,
            "instruction": "如果 new_question 与某个候选题本质相同或高度相似，返回该候选题 id；否则返回 null。只返回 JSON：{\"match_id\": 123 或 null}",
        }
        try:
            target = self._build_llm_targets()[0]
            client = self._build_client(api_key=target["api_key"], base_url=target["base_url"])
            response = client.chat.completions.create(
                model=target["model"],
                messages=[
                    {"role": "system", "content": "你是面试题去重助手，只判断题目是否本质相同。只返回 JSON。"},
                    {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
                ],
            )
            payload = json.loads(self._extract_json_text(self._extract_output_text(response)))
            match_id = payload.get("match_id")
            if match_id is None:
                return None
            return int(match_id)
        except Exception:
            return None

    def suggest_taxonomy_category(self, categories: list[str], new_question: str, points: list[str]) -> str | None:
        categories = [str(item).strip() for item in categories if str(item).strip()]
        if not categories:
            return None

        prompt = {
            "available_categories": categories,
            "new_question": new_question,
            "question_points": points,
            "instruction": (
                "如果现有分类都不合适，请建议一个新的一级目录名。"
                "要求名称简短、稳定、适合作为知识库长期目录。"
                "只返回 JSON：{\"suggested_category\": \"...\"}，如果不需要新目录则返回 null。"
            ),
        }
        try:
            target = self._build_llm_targets()[0]
            client = self._build_client(api_key=target["api_key"], base_url=target["base_url"])
            response = client.chat.completions.create(
                model=target["model"],
                messages=[
                    {
                        "role": "system",
                        "content": "你是知识库分类助手，只判断是否需要新增一级目录。只返回 JSON。",
                    },
                    {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
                ],
            )
            payload = json.loads(self._extract_json_text(self._extract_output_text(response)))
            suggested = str(payload.get("suggested_category") or "").strip()
            return suggested or None
        except Exception:
            return None

    def _build_client(self, *, api_key: Optional[str], base_url: Optional[str]) -> OpenAI:
        return OpenAI(
            api_key=(api_key or "").strip() or None,
            base_url=base_url or None,
            timeout=float(self.settings.llm_request_timeout_seconds),
            max_retries=0,
        )

    def _build_llm_targets(self) -> list[dict]:
        primary = {
            "name": "primary",
            "api_key": self.settings.openai_api_key,
            "base_url": self.settings.openai_base_url,
            "model": self.settings.openai_model,
        }
        targets = [primary]
        if (self.settings.openai_backup_model or "").strip():
            targets.append(
                {
                    "name": "backup",
                    "api_key": (self.settings.openai_backup_api_key or self.settings.openai_api_key),
                    "base_url": self.settings.openai_backup_base_url or self.settings.openai_base_url,
                    "model": self.settings.openai_backup_model,
                }
            )
        return targets

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
            content_type=LLMAnalyzer._infer_fallback_content_type(text),
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

    @classmethod
    def _apply_short_text_heuristics(cls, payload: dict, title: Optional[str], raw_text: Optional[str]) -> dict:
        normalized = dict(payload)
        text = "\n".join(part for part in [title, raw_text] if part).strip()
        question = cls._extract_single_question(raw_text or title or "")
        if not question:
            return normalized

        content_type = str(normalized.get("content_type") or "").strip().lower()
        is_interview = bool(normalized.get("is_interview_experience"))
        questions = cls._coerce_list(normalized.get("interview_questions"))
        points = cls._coerce_list(normalized.get("question_points"))

        if content_type in {"noise", ""} or not is_interview:
            normalized["content_type"] = "knowledge_snippet"
            normalized["is_interview_experience"] = True

        if not questions:
            normalized["interview_questions"] = [question]

        if not points:
            inferred_points = cls._infer_points_from_question(question)
            if inferred_points:
                normalized["question_points"] = inferred_points

        if not normalized.get("summary"):
            normalized["summary"] = f"记录了一条简短面试问题：{question}"

        return normalized

    @staticmethod
    def _extract_single_question(text: str) -> Optional[str]:
        cleaned = str(text or "").strip()
        if not cleaned:
            return None
        if len(cleaned) > 120:
            return None

        markers = ["问到", "问了", "怎么", "怎么办", "为什么", "区别", "实现", "如何", "哪些", "原理"]
        if any(marker in cleaned for marker in markers):
            return cleaned.strip("：:;；。 ")
        return None

    @staticmethod
    def _infer_points_from_question(question: str) -> list[str]:
        lowered = question.lower()
        inferred = []
        mapping = [
            ("https", "HTTPS"),
            ("证书", "数字证书"),
            ("锁", "锁机制"),
            ("redis", "Redis"),
            ("mysql", "MySQL"),
            ("幂等", "幂等性"),
            ("分布式", "分布式系统"),
        ]
        for keyword, point in mapping:
            if keyword in lowered:
                inferred.append(point)
        return inferred

    @staticmethod
    def _normalize_content_type(value: Optional[str], questions: list[str], is_interview_experience: bool) -> str:
        normalized = str(value or "").strip().lower()
        allowed = {"interview_note", "knowledge_snippet", "algorithm_snippet", "noise"}
        if normalized in allowed:
            return normalized
        if not is_interview_experience:
            return "noise"
        return LLMAnalyzer._infer_content_type_from_questions(questions)

    @staticmethod
    def _infer_content_type_from_questions(questions: list[str]) -> str:
        if not questions:
            return "knowledge_snippet"
        algorithm_hits = 0
        markers = ["算法", "手撕", "leetcode", "hot100", "二叉树", "链表", "动态规划", "排序", "数组"]
        for item in questions:
            text = str(item or "").lower()
            if any(marker.lower() in text for marker in markers):
                algorithm_hits += 1
        if algorithm_hits and algorithm_hits >= max(1, len(questions) // 2):
            return "algorithm_snippet"
        if len(questions) >= 4:
            return "interview_note"
        return "knowledge_snippet"

    @staticmethod
    def _infer_fallback_content_type(text: str) -> str:
        lowered = str(text or "").lower()
        if not lowered.strip():
            return "noise"
        if any(keyword in lowered for keyword in ["算法", "手撕", "leetcode", "hot100"]):
            return "algorithm_snippet"
        if any(keyword in lowered for keyword in ["面经", "一面", "二面", "三面", "hr面", "oc"]):
            return "interview_note"
        return "knowledge_snippet"
