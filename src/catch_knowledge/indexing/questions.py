from __future__ import annotations

import re

from sqlalchemy.orm import Session

from catch_knowledge.db.models import CanonicalQuestion, PostAnalysis, RawPost, TaxonomySuggestion
from catch_knowledge.llm import LLMAnalyzer


class QuestionIndexBuilder:
    """Build a stable question index for Obsidian.

    The first-level knowledge point is intentionally controlled by code instead
    of left entirely to the LLM. LLM-extracted fine-grained points are kept as
    subtopics in `variants`, so we get a clean directory structure without
    throwing away useful detail.
    """

    taxonomy = [
        ("算法题", ["手撕", "leetcode", "hot100", "算法题", "二叉树", "动态规划", "最大子数组", "无重复子串", "合并两个有序链表", "大数加法", "表达式求值", "打家劫舍"]),
        ("Redis", ["redis", "缓存", "布隆过滤器", "穿透", "雪崩", "击穿", "逻辑过期", "lua", "库存", "big key", "淘汰", "内存"]),
        ("MySQL", ["mysql", "数据库", "索引", "事务", "隔离级别", "分库分表", "sql", "orm", "乐观锁", "悲观锁"]),
        ("消息队列", ["消息队列", "mq", "kafka", "rocketmq", "消息分发", "投递", "顺序消息"]),
        ("分布式系统", ["分布式", "一致性", "幂等", "去重", "状态机", "token", "jwt", "权限", "分布式锁"]),
        ("Java并发", ["线程池", "aqs", "并发", "多线程", "锁", "volatile", "synchronized", "阻塞队列", "无界队列"]),
        ("JVM", ["jvm", "gc", "内存管理", "引用", "类加载"]),
        ("Java基础", ["java", "集合", "hashmap", "基础知识", "引用"]),
        ("Spring", ["spring", "springboot", "注解", "bean", "ioc", "aop"]),
        ("计算机网络", ["http", "https", "tcp", "udp", "quic", "网络协议", "浏览器页面加载"]),
        ("操作系统", ["进程", "线程切换", "操作系统", "内存", "调度"]),
        ("系统设计", ["系统设计", "秒杀", "高并发", "架构", "服务", "支付", "订单", "登录系统", "排行榜", "超卖", "防刷"]),
        ("AI/RAG", ["rag", "知识库", "bm25", "召回", "重排", "embedding", "向量库", "chunk", "查询改写", "检索增强"]),
        ("Agent开发", ["agent", "function calling", "tool calling", "workflow", "multi-agent", "planner", "memory", "智能体", "任务编排", "工具调用"]),
        ("LLM应用工程", ["ai", "大模型", "prompt", "结构化输出", "模型路由", "模型降级", "token", "评测", "观测", "推理", "ai coding"]),
        ("项目经历", ["项目", "实习", "科研", "业务", "难点", "技术栈", "项目经验", "项目介绍"]),
        ("HR/行为面", ["自我介绍", "职业规划", "沟通", "兴趣", "转行", "地域", "团队协作", "留用", "行为面"]),
        ("工程实践", ["排查", "性能优化", "日志", "elk", "elasticsearch", "动态配置", "oss", "代码质量", "责任链", "ddd", "领域驱动"]),
    ]

    algorithm_category = "算法题"
    fallback_category = "未分类"

    def __init__(self, analyzer: LLMAnalyzer):
        self.analyzer = analyzer

    def rebuild(self, session: Session) -> dict:
        session.query(CanonicalQuestion).delete()
        session.query(TaxonomySuggestion).delete()
        session.flush()

        stats = {
            "questions_seen": 0,
            "canonical_questions": 0,
            "merged": 0,
            "created": 0,
            "taxonomy_suggestions": 0,
        }
        rows = (
            session.query(RawPost, PostAnalysis)
            .join(PostAnalysis, PostAnalysis.raw_post_id == RawPost.id)
            .filter(PostAnalysis.is_interview_experience.is_(True))
            .order_by(RawPost.id.asc())
            .all()
        )

        for raw_post, analysis in rows:
            questions = analysis.interview_questions or []
            points = analysis.question_points or []
            for question in questions:
                clean_question = self._clean(question)
                if not clean_question:
                    continue

                stats["questions_seen"] += 1
                category = self._classify_question(clean_question, points)
                if category == self.fallback_category:
                    suggestion_name = self._suggest_category(clean_question, points)
                    if suggestion_name:
                        self._record_suggestion(session, suggestion_name, raw_post, clean_question)
                        stats["taxonomy_suggestions"] += 1
                kind = "algorithm" if category == self.algorithm_category else "interview"
                subtopics = self._matching_subtopics(clean_question, points, category)

                matched = self._find_match(session, kind, category, clean_question)
                if matched:
                    self._add_occurrence(matched, raw_post, clean_question, subtopics)
                    stats["merged"] += 1
                    continue

                created = CanonicalQuestion(
                    kind=kind,
                    knowledge_point=category,
                    canonical_text=clean_question,
                    frequency=1,
                    source_raw_post_ids=[raw_post.id],
                    variants=[
                        {
                            "raw_post_id": raw_post.id,
                            "question": clean_question,
                            "subtopics": subtopics,
                        }
                    ],
                )
                session.add(created)
                session.flush()
                stats["created"] += 1

        stats["canonical_questions"] = session.query(CanonicalQuestion).count()
        session.commit()
        return stats

    def sync_posts(self, session: Session, raw_post_ids: list[int]) -> dict:
        if not raw_post_ids:
            return {
                "questions_seen": 0,
                "canonical_questions": session.query(CanonicalQuestion).count(),
                "merged": 0,
                "created": 0,
                "taxonomy_suggestions": 0,
                "removed_occurrences": 0,
            }

        impacted_ids = {int(raw_post_id) for raw_post_id in raw_post_ids}
        stats = {
            "questions_seen": 0,
            "canonical_questions": 0,
            "merged": 0,
            "created": 0,
            "taxonomy_suggestions": 0,
            "removed_occurrences": 0,
        }

        stats["removed_occurrences"] = self._remove_existing_occurrences(session, impacted_ids)

        rows = (
            session.query(RawPost, PostAnalysis)
            .join(PostAnalysis, PostAnalysis.raw_post_id == RawPost.id)
            .filter(
                RawPost.id.in_(list(impacted_ids)),
                PostAnalysis.is_interview_experience.is_(True),
            )
            .order_by(RawPost.id.asc())
            .all()
        )

        for raw_post, analysis in rows:
            questions = analysis.interview_questions or []
            points = analysis.question_points or []
            for question in questions:
                clean_question = self._clean(question)
                if not clean_question:
                    continue

                stats["questions_seen"] += 1
                category = self._classify_question(clean_question, points)
                if category == self.fallback_category:
                    suggestion_name = self._suggest_category(clean_question, points)
                    if suggestion_name:
                        self._record_suggestion(session, suggestion_name, raw_post, clean_question)
                        stats["taxonomy_suggestions"] += 1
                kind = "algorithm" if category == self.algorithm_category else "interview"
                subtopics = self._matching_subtopics(clean_question, points, category)

                matched = self._find_match(session, kind, category, clean_question)
                if matched:
                    self._add_occurrence(matched, raw_post, clean_question, subtopics)
                    stats["merged"] += 1
                    continue

                created = CanonicalQuestion(
                    kind=kind,
                    knowledge_point=category,
                    canonical_text=clean_question,
                    frequency=1,
                    source_raw_post_ids=[raw_post.id],
                    variants=[
                        {
                            "raw_post_id": raw_post.id,
                            "question": clean_question,
                            "subtopics": subtopics,
                        }
                    ],
                )
                session.add(created)
                session.flush()
                stats["created"] += 1

        stats["canonical_questions"] = session.query(CanonicalQuestion).count()
        return stats

    def _suggest_category(self, question: str, points: list[str]) -> str | None:
        suggested = self.analyzer.suggest_taxonomy_category(self.available_categories(), question, points)
        clean_name = self._clean(suggested)
        if not clean_name:
            return None
        if clean_name in self.available_categories() or clean_name == self.fallback_category:
            return None
        return clean_name[:80]

    def _find_match(self, session: Session, kind: str, point: str, question: str):
        candidates = (
            session.query(CanonicalQuestion)
            .filter(CanonicalQuestion.kind == kind, CanonicalQuestion.knowledge_point == point)
            .order_by(CanonicalQuestion.frequency.desc(), CanonicalQuestion.id.asc())
            .all()
        )
        normalized = self._normalize(question)
        for candidate in candidates:
            candidate_normalized = self._normalize(candidate.canonical_text)
            if candidate_normalized == normalized:
                return candidate
            if self._is_near_duplicate(candidate_normalized, normalized):
                return candidate

        return None

    def _remove_existing_occurrences(self, session: Session, impacted_ids: set[int]) -> int:
        removed_occurrences = 0

        for canonical in session.query(CanonicalQuestion).all():
            source_ids = [raw_post_id for raw_post_id in (canonical.source_raw_post_ids or []) if raw_post_id not in impacted_ids]
            variants = []
            for variant in canonical.variants or []:
                if not isinstance(variant, dict):
                    continue
                if variant.get("raw_post_id") in impacted_ids:
                    removed_occurrences += 1
                    continue
                variants.append(variant)

            if not variants:
                session.delete(canonical)
                continue

            canonical.source_raw_post_ids = source_ids
            canonical.variants = variants
            canonical.frequency = len(variants)

        for suggestion in session.query(TaxonomySuggestion).all():
            source_ids = [raw_post_id for raw_post_id in (suggestion.source_raw_post_ids or []) if raw_post_id not in impacted_ids]
            if not source_ids:
                session.delete(suggestion)
                continue
            suggestion.source_raw_post_ids = source_ids
            suggestion.frequency = len(source_ids)

        session.flush()
        return removed_occurrences

    @staticmethod
    def _add_occurrence(canonical: CanonicalQuestion, raw_post: RawPost, question: str, subtopics: list[str]) -> None:
        source_ids = list(canonical.source_raw_post_ids or [])
        variants = list(canonical.variants or [])
        if raw_post.id not in source_ids:
            source_ids.append(raw_post.id)
        variants.append({"raw_post_id": raw_post.id, "question": question, "subtopics": subtopics})
        canonical.source_raw_post_ids = source_ids
        canonical.variants = variants
        canonical.frequency = len(variants)

    @staticmethod
    def _record_suggestion(session: Session, suggested_name: str, raw_post: RawPost, question: str) -> None:
        suggestion = (
            session.query(TaxonomySuggestion)
            .filter(TaxonomySuggestion.suggested_name == suggested_name)
            .one_or_none()
        )
        if suggestion is None:
            suggestion = TaxonomySuggestion(
                suggested_name=suggested_name,
                status="pending",
                frequency=1,
                source_raw_post_ids=[raw_post.id],
                example_questions=[question],
            )
            session.add(suggestion)
            session.flush()
            return

        source_ids = list(suggestion.source_raw_post_ids or [])
        if raw_post.id not in source_ids:
            source_ids.append(raw_post.id)
        examples = list(suggestion.example_questions or [])
        if question not in examples:
            examples.append(question)
        suggestion.source_raw_post_ids = source_ids
        suggestion.example_questions = examples[:10]
        suggestion.frequency = int(suggestion.frequency or 0) + 1

    def _classify_question(self, question: str, points: list[str]) -> str:
        if self._is_algorithm_question(question, points):
            return self.algorithm_category

        normalized_question = self._normalize(question)
        for category, keywords in self.taxonomy:
            if category == self.algorithm_category:
                continue
            if any(self._normalize(keyword) in normalized_question for keyword in keywords):
                return category

        for point in points:
            clean_point = self._clean(point)
            if not clean_point or not self._point_matches_question(clean_point, question):
                continue
            point_category = self._classify_point(clean_point)
            if point_category != self.fallback_category:
                return point_category

        return self.fallback_category

    def _classify_point(self, point: str) -> str:
        normalized_point = self._normalize(point)
        for category, keywords in self.taxonomy:
            if any(self._normalize(keyword) in normalized_point for keyword in keywords):
                return category
        return self.fallback_category

    def _point_matches_question(self, point: str, question: str) -> bool:
        normalized_point = self._normalize(point)
        normalized_question = self._normalize(question)
        if not normalized_point or not normalized_question:
            return False
        if normalized_point in normalized_question:
            return True

        point_tokens = self._tokens(normalized_point)
        question_tokens = self._tokens(normalized_question)
        if not point_tokens or not question_tokens:
            return False
        return len(point_tokens & question_tokens) / len(point_tokens) >= 0.45

    def _is_algorithm_question(self, question: str, points: list[str]) -> bool:
        haystack = self._normalize(question)
        strong_markers = ["手撕", "leetcode", "hot100", "算法题"]
        if any(self._normalize(marker) in haystack for marker in strong_markers):
            return True

        algorithm_phrases = [
            "二叉树",
            "动态规划",
            "最大子数组",
            "无重复子串",
            "有序链表",
            "大数加法",
            "表达式求值",
            "打家劫舍",
            "先序遍历",
            "最近公共祖先",
        ]
        return any(self._normalize(phrase) in haystack for phrase in algorithm_phrases)

    def _matching_subtopics(self, question: str, points: list[str], category: str) -> list[str]:
        selected = []
        normalized_question = self._normalize(question)
        for point in points:
            clean_point = self._clean(point)
            if not clean_point:
                continue
            if self._normalize(clean_point) in normalized_question or self._classify_point(clean_point) == category:
                selected.append(clean_point)
        return sorted(set(selected))

    def available_categories(self) -> list[str]:
        return [category for category, _ in self.taxonomy]

    @staticmethod
    def _clean(value) -> str:
        return str(value or "").strip()

    @staticmethod
    def _normalize(value: str) -> str:
        text = str(value or "").lower()
        text = re.sub(r"[\s，。！？、,.!?;；：:（）()\[\]【】\"'`]+", "", text)
        return text

    @staticmethod
    def _is_near_duplicate(left: str, right: str) -> bool:
        if not left or not right:
            return False
        shorter, longer = sorted([left, right], key=len)
        if len(shorter) >= 8 and shorter in longer:
            return True

        left_tokens = QuestionIndexBuilder._tokens(left)
        right_tokens = QuestionIndexBuilder._tokens(right)
        if not left_tokens or not right_tokens:
            return False
        overlap = len(left_tokens & right_tokens)
        union = len(left_tokens | right_tokens)
        return union > 0 and overlap / union >= 0.86

    @staticmethod
    def _tokens(value: str) -> set[str]:
        ascii_tokens = set(re.findall(r"[a-z0-9]+", value.lower()))
        cjk_tokens = {value[index : index + 2] for index in range(max(len(value) - 1, 0))}
        return ascii_tokens | cjk_tokens
