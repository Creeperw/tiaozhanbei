from __future__ import annotations

import logging
import re
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from competition_app.agents.common import envelope
from competition_app.agents.paper_blueprint_compiler import PaperBlueprintCompilerAgent
from competition_app.contracts.agent_context import build_model_context
from competition_app.contracts.base import AgentEnvelope
from competition_app.contracts.paper import BlueprintUnit, PaperBlueprint
from competition_app.llm.base import ChatModel
from competition_app.llm.prompt_skills import prompt_skill_registry
from competition_app.llm.stub import StubChatModel
from competition_app.services.smart_paper import stable_scope_digest

logger = logging.getLogger(__name__)


class PaperBlueprintAgent:
    """Expert stage one: design retrieval-ready blueprint units before retrieval."""

    # 编译器未提取到整卷题量、且用户也没给出结构化题量时，单元的建议规模。
    # 它只是检索与生成的起点，不是发布硬门槛（见
    # ``question_count_is_hard_constraint``）。
    _FALLBACK_UNCONSTRAINED_UNIT_QUESTION_COUNT = 10


    def __init__(
        self,
        chat_model: ChatModel | None = None,
        blueprint_compiler: PaperBlueprintCompilerAgent | None = None,
    ) -> None:
        self.chat_model = chat_model or StubChatModel()
        self.blueprint_compiler = (
            blueprint_compiler or PaperBlueprintCompilerAgent(self.chat_model)
        )

    async def run(self, context: dict[str, Any]) -> AgentEnvelope[PaperBlueprint]:
        if context.get("smart_paper_v2") is True:
            return self._run_structured_smart_paper(context)
        skill = prompt_skill_registry.load("expert_agent", "paper_blueprint")
        conversation_scope = self._conversation_scope_context(context)
        blueprint_request = str(context.get("user_request") or "")
        if conversation_scope:
            blueprint_request = (
                f"{blueprint_request}\n"
                "本轮指代所承接的上一轮学习主题："
                f"{conversation_scope['previous_user_request']}"
            )
        # 整卷硬约束只从系统级约束通道读取，不从用户原话里用关键词猜：
        # 自由文本的语义由蓝图模型负责，编译器负责把原稿里明写的约束逐字提取
        # 回来（见下方 compiled 分支）。取不到时不设硬约束。
        explicit_distribution = self._explicit_question_type_distribution(context)
        explicit_count = (
            sum(explicit_distribution.values())
            if explicit_distribution
            else self._explicit_question_count(context)
        )
        learning_progress = self._learning_progress_payload(
            context.get("dependency_outputs", {}).get("diagnosis")
        )
        try:
            # The blueprint is a business-agent document, not an execution
            # contract.  Stream the natural-language draft directly; the
            # dedicated compiler below remains the only component allowed to
            # turn it into structured fields.
            prose_completion = getattr(self.chat_model, "complete_text", None)
            if not callable(prose_completion):
                prose_completion = self.chat_model.complete_json
            raw_output = await prose_completion(
                    "expert_agent",
                    build_model_context(
                        context,
                        target_agent="expert_agent",
                        prompt_skill=skill,
                        payload={
                            "phase": "paper_blueprint",
                            "blueprint_output_mode": "natural_language_document",
                            "user_request": blueprint_request,
                            "conversation_scope": conversation_scope,
                            "exam_constraints": context.get("exam_constraints", {}),
                            "session_time_budget_minutes": context.get("available_minutes"),
                            "learning_scope": self._requested_learning_scope(context),
                            "planning_context": self._planning_context(context),
                            "learning_progress": learning_progress,
                            "user_profile": context.get("user_profile", {}),
                            "output_contract": {
                                "blueprint_document": (
                                "一篇完整、详细、可读的自然语言试卷蓝图原稿；"
                                "正文必须用独立单行明确“试卷标题”和“范围摘要”，"
                                "并用独立单行写每个单元的学习目标、检索表达、"
                                "题型偏好、目标题数、可选分值、假设和验收条件。"
                            )
                            },
                        },
                        permission_note=(
                            "只生成详细自然语言试卷蓝图原稿和分单元检索需求；不得检索题目、选择题目、"
                            "生成试卷正文、答案、解析、系统ID或系统未提供的评级字段。"
                        ),
                    ),
                )
            blueprint_document = str(raw_output).strip()
            if not blueprint_document:
                raise ValueError("paper blueprint agent did not provide natural-language draft")
            compilation = await self.blueprint_compiler.compile(
                context,
                blueprint_document=blueprint_document,
            )
            if compilation.result.status == "compiled":
                contract = compilation.result.contract
                # 整卷硬约束以蓝图原稿为准：它是本轮用户意图的唯一事实来源，
                # 而且每条值都刚通过了逐字锚点校验。编译器没提取到就不设硬
                # 约束，让蓝图自己的单元题数说话——取不到时保持 None，而不
                # 是拿一个猜出来的近似值当发布门禁。
                if contract.question_type_distribution:
                    explicit_distribution = {
                        str(question_type): int(count)
                        for question_type, count in (
                            contract.question_type_distribution.items()
                        )
                        if int(count) > 0
                    }
                explicit_count = (
                    sum(explicit_distribution.values())
                    if explicit_distribution
                    else (
                        contract.required_question_count
                        or self._explicit_question_count(context)
                    )
                )
                normalized = {
                    "title": contract.title,
                    "scope_summary": contract.scope_summary,
                    "duration_minutes": contract.duration_minutes,
                    "total_score": contract.total_score,
                    "assumptions": contract.assumptions,
                    "acceptance_criteria": contract.acceptance_criteria,
                    "requires_explanation": contract.requires_explanation,
                    "units": [
                        unit.model_dump(mode="python", exclude={"unit_key"})
                        for unit in contract.units
                    ],
                }
            else:
                # A prose-to-contract extraction miss is not a reason to
                # terminate paper generation.  The current user message
                # already provides the system-owned topic/count/type/
                # difficulty constraints needed for a safe minimal blueprint.
                # Discard the uncompiled prose and continue from only those
                # explicit values; do not infer hidden plan content.
                #
                # 降级本身保留，但必须留下可查证据：编译器给出的 issues 只
                # 存在于这一次调用的返回值里，不落库也不打日志，线上只能看到
                # “试卷内容不对”而无法定位到是哪条锚点失配。
                logger.warning(
                    "paper blueprint compilation did not compile; "
                    "falling back to explicit request constraints: "
                    "status=%s source_digest=%s issues=%s",
                    compilation.result.status,
                    compilation.source_digest,
                    [
                        {
                            "code": getattr(issue, "code", ""),
                            "field_path": getattr(issue, "field_path", ""),
                            "detail": getattr(issue, "detail", ""),
                        }
                        for issue in getattr(compilation.result, "issues", []) or []
                    ],
                )
                normalized = self._fallback_blueprint_from_request(
                    context,
                    explicit_count=explicit_count,
                    explicit_types=(
                        list(explicit_distribution)
                        if explicit_distribution
                        else self._explicit_question_types(context)
                    ),
                )
            normalized = self._normalize_blueprint(normalized, context)
            normalized["units"] = self._constrain_units_to_explicit_coverage(
                normalized.get("units", []),
                coverage_topics=self._explicit_coverage_topics(context),
            )
            normalized["units"] = self._normalize_hard_count_units(
                normalized.get("units", []),
                explicit_count=explicit_count,
                has_explicit_distribution=bool(explicit_distribution),
            )
            normalized["units"] = self._normalize_question_type_mix(
                normalized.get("units", []),
                explicit_types=(
                    list(explicit_distribution)
                    if explicit_distribution
                    else self._explicit_question_types(context)
                ),
                has_explicit_distribution=bool(explicit_distribution),
            )
            units = [
                BlueprintUnit(
                    unit_id=f"UNIT_{index:02d}",
                    sequence=index,
                    candidate_limit=self._candidate_limit(
                        int(unit.get("required_question_count") or 1)
                    ),
                    **{
                        key: value
                        for key, value in unit.items()
                        if key != "candidate_limit"
                    },
                )
                for index, unit in enumerate(normalized["units"], start=1)
            ]
        except ValidationError as exc:
            raise ValueError("paper blueprint model output violates protocol") from exc
        blueprint = PaperBlueprint(
            blueprint_id=f"BLUEPRINT_{uuid4().hex}",
            title=normalized["title"],
            source_status=normalized["source_status"],
            scope_summary=normalized["scope_summary"],
            duration_minutes=normalized.get("duration_minutes"),
            total_score=normalized.get("total_score"),
            required_total_question_count=explicit_count,
            required_question_type_distribution=explicit_distribution,
            question_count_is_hard_constraint=(
                explicit_count is not None
            ),
            requires_explanation=bool(normalized.get("requires_explanation", False)),
            units=units,
            assumptions=normalized.get("assumptions", []),
            acceptance_criteria=normalized.get("acceptance_criteria", []),
        )
        return envelope(context, "expert_agent", "paper_blueprint", blueprint)

    @classmethod
    def _run_structured_smart_paper(
        cls, context: dict[str, Any]
    ) -> AgentEnvelope[PaperBlueprint]:
        """Compile the dedicated workshop form without an authoring model.

        All executable fields come from the server-validated form contract.
        User topic text remains retrieval data and is never interpreted as a
        prompt, route, tool request, identifier, or persistence instruction.
        """

        constraints = dict(context.get("exam_constraints") or {})
        distribution = {
            str(question_type): int(count)
            for question_type, count in dict(
                constraints.get("question_type_distribution") or {}
            ).items()
            if isinstance(count, int) and not isinstance(count, bool) and count > 0
        }
        required_total = sum(distribution.values())
        learning_progress = cls._learning_progress_payload(
            context.get("dependency_outputs", {}).get("diagnosis")
        )
        if constraints.get("paper_kind") == "adaptive":
            # Diagnosis is server-owned, read-only evidence from the learner's
            # current exam workspace.  Prefer its weakest/due knowledge points,
            # then use the already bounded UI recommendations only as a safe
            # fallback.  Neither source may inject routes, tools or IDs into the
            # execution contract; only plain topic labels are retained.
            topics = cls._adaptive_topics_from_learning_progress(learning_progress)
            topics.extend(list(constraints.get("focus_topics") or []))
        else:
            # An explicitly selected special-practice topic is a hard user
            # boundary.  Diagnosis may affect candidate ranking later, but it
            # must never silently broaden or replace this scope.
            topics = [str(constraints.get("topic") or "").strip()]
        topics = [str(topic).strip()[:120] for topic in topics if str(topic).strip()]
        topics = list(dict.fromkeys(topics))[:8]
        if not topics or required_total < 1:
            raise ValueError("structured smart paper constraints are incomplete")

        base_count, remainder = divmod(required_total, len(topics))
        units: list[BlueprintUnit] = []
        for index, topic in enumerate(topics, start=1):
            unit_count = base_count + (1 if index <= remainder else 0)
            if unit_count < 1:
                continue
            units.append(
                BlueprintUnit(
                    unit_id=f"UNIT_{index:02d}",
                    sequence=index,
                    knowledge_module=topic,
                    learning_objective=f"检验并巩固{topic}的核心知识",
                    retrieval_query=topic,
                    question_type_preferences=list(distribution),
                    required_question_count=unit_count,
                    candidate_limit=cls._candidate_limit(unit_count),
                    selection_rules=[
                        "只采用与本单元直接相关且题干、答案完整的候选题。",
                        "候选题身份、来源与难度只接受系统绑定字段。",
                    ],
                    target_difficulty=constraints.get("difficulty"),
                    difficulty_is_hard_constraint=(
                        constraints.get("difficulty") is not None
                    ),
                    difficulty_fallback_policy=(
                        "unlabeled_official"
                        if constraints.get("difficulty") is not None
                        else "strict"
                    ),
                )
            )
        scope = "、".join(topics)
        mode_label = "测试" if constraints.get("answer_mode") == "test" else "练习"
        # 解析交付项只来自表单的独立开关，不从主题正文推断：主题正文是检索
        # 数据，可能只是引用题面或随口提到解析，把它当交付条件会静默改变可用
        # 候选范围（无解析的正式题会被整批挡在卷外）。
        requires_explanation = bool(constraints.get("requires_explanation", False))
        blueprint = PaperBlueprint(
            blueprint_id=(
                f"BLUEPRINT_SMART_{stable_scope_digest(scope)}_{uuid4().hex[:12]}"
            ),
            title=f"{scope[:80]}{mode_label}试卷",
            source_status="practice_sample",
            scope_summary=scope,
            duration_minutes=constraints.get("duration_minutes"),
            total_score=None,
            required_total_question_count=required_total,
            required_question_type_distribution=distribution,
            question_count_is_hard_constraint=True,
            requires_explanation=requires_explanation,
            units=units,
            assumptions=[
                "本蓝图由系统根据训练工坊表单直接编译，未让模型改写题量、题型、范围或难度。"
            ],
            acceptance_criteria=[
                f"最终题量必须为{required_total}题。",
                "最终题型数量必须与用户提交的结构化分布完全一致。",
                "题目范围不得超出结构化主题或学情推荐知识点。",
                *(["每题必须附解析。"] if requires_explanation else []),
                # 发布门槛只卡「审核有没有形成语义结论」。审核有结论但指出问题
                # 时试卷照常发布，问题必须写进卷面的「审核说明」，不得隐藏：
                # 候选不足时组卷会在缺口上按降级策略补题，这类卷子长期带着
                # 非阻塞问题，一律扣下会让学习者拿不到任何东西。
                "试卷发布只要求内容审核形成语义结论（审核器输出符合协议）；"
                "审核指出的问题必须在卷面「审核说明」中如实列出。",
            ],
        )
        return envelope(context, "paper_blueprint_agent", "paper_blueprint", blueprint)

    @staticmethod
    def _adaptive_topics_from_learning_progress(
        learning_progress: dict[str, Any],
    ) -> list[str]:
        """Extract bounded topic labels from observed Diagnosis evidence only."""

        if not learning_progress.get("available"):
            return []
        snapshot = learning_progress.get("snapshot")
        if not isinstance(snapshot, dict):
            return []
        mastery_view = snapshot.get("mastery_and_review")
        if not isinstance(mastery_view, dict):
            mastery_view = snapshot
        rows = mastery_view.get("mastery")
        if not isinstance(rows, list):
            rows = []
        ordered_rows = sorted(
            (row for row in rows if isinstance(row, dict)),
            key=lambda row: (
                not bool(row.get("requires_remediation")),
                float(row.get("mastery_score") or 0.0),
                str(row.get("kp_name") or row.get("name") or ""),
            ),
        )
        topics: list[str] = []
        for row in ordered_rows:
            topic = str(row.get("kp_name") or row.get("name") or "").strip()
            # Internal IDs are not useful retrieval topics and must not leak
            # into model/tool text when a human-readable name is unavailable.
            if not topic or topic == str(row.get("kp_id") or "").strip():
                continue
            topics.append(topic[:120])
            if len(topics) >= 8:
                break
        return list(dict.fromkeys(topics))

    @classmethod
    def _fallback_blueprint_from_request(
        cls,
        context: dict[str, Any],
        *,
        explicit_count: int | None,
        explicit_types: list[str],
    ) -> dict[str, Any]:
        """编译器未能提取合同时的最小可用蓝图。

        这里不得再用正则从用户原话里切主题或题量：切出来的片段会被当作标题
        直接展示给用户（此前把用户原话前 300 字加“练习试卷”当成了试卷标题）。
        只使用系统级约束通道里的结构化值；没有值就保持中性默认，并在假设里
        明确告知这是系统建议而不是用户要求。
        """

        request = str(context.get("user_request") or "").strip()
        constraints = context.get("exam_constraints", {}) or {}
        scope = str(constraints.get("topic") or "").strip()
        retrieval_scope = scope or request[:300].strip() or "用户指定主题"
        difficulty = cls._explicit_difficulty(context)
        assumptions = [
            "系统未能从试卷蓝图原稿中提取到可校验的执行合同，"
            "已改用用户当前消息中可结构化确认的约束继续组卷。"
        ]
        if explicit_count is None:
            count = cls._FALLBACK_UNCONSTRAINED_UNIT_QUESTION_COUNT
            assumptions.append(
                f"用户未明确写出整卷题量，单元先按{count}题的建议规模组卷，"
                "该题量不是发布硬门槛。"
            )
        else:
            count = explicit_count
        return {
            "title": f"{scope}练习试卷" if scope else "",
            "scope_summary": scope or request[:1000].strip(),
            "duration_minutes": None,
            "total_score": None,
            "assumptions": assumptions,
            "acceptance_criteria": [
                *(
                    [f"最终题量为{count}题。"]
                    if explicit_count is not None
                    else []
                ),
                "题目范围只覆盖用户当前请求指定的主题。",
                "答案与解析必须通过审核后才能发布。",
            ],
            # 降级路径不再从用户原话里猜交付条件：编译器没能把解析要求
            # 逐字提取回来时，按“不强制逐题解析”继续，并在假设里如实告知，
            # 而不是用关键词猜测后静默改变可用候选的范围。
            "requires_explanation": False,
            "units": [
                {
                    "knowledge_module": retrieval_scope,
                    "learning_objective": f"完成{retrieval_scope}的基础练习",
                    "retrieval_query": retrieval_scope,
                    "question_type_preferences": list(explicit_types),
                    "required_question_count": count,
                    "candidate_limit": cls._candidate_limit(count),
                    "selection_rules": [
                        "只选择与当前主题直接相关且题干、选项、答案完整的题目。"
                    ],
                    "target_difficulty": difficulty,
                    "difficulty_is_hard_constraint": difficulty is not None,
                }
            ],
        }

    @staticmethod
    def _conversation_scope_context(context: dict[str, Any]) -> dict[str, Any]:
        """Resolve anaphoric paper requests from the nearest prior user turn.

        Planner owns task routing, while the blueprint agent owns the actual
        exam scope.  A request such as “给我一套相关试卷” is therefore routed
        correctly but still needs its antecedent supplied here; otherwise the
        much larger persisted learning plan can incorrectly become the topic.
        """

        request = str(context.get("user_request") or "").strip()
        reference_tokens = (
            "相关",
            "这个",
            "上述",
            "刚才",
            "前面",
            "同主题",
            "对应",
        )
        if not request or not any(
            token in request
            for token in reference_tokens
        ):
            return {}
        skipped_current = False
        for message in reversed(list(context.get("messages") or [])):
            if not isinstance(message, dict) or message.get("role") != "user":
                continue
            content = str(message.get("content") or "").strip()
            if not content:
                continue
            if not skipped_current and content == request:
                skipped_current = True
                continue
            if content == request:
                continue
            if (
                any(token in content for token in ("试卷", "组卷", "套题"))
                and any(token in content for token in reference_tokens)
            ):
                # A failed/retried “相关试卷” turn does not become its own
                # topic. Continue walking back to the explicit learning turn.
                continue
            return {
                "is_contextual_followup": True,
                "previous_user_request": content[:800],
                "resolution_rule": (
                    "本轮未另行指定范围时，试卷只承接该上一轮学习主题；"
                    "不得改用画像目标、长期阶段或短期计划作为试卷主题。"
                ),
            }
        return {}

    @staticmethod
    def _learning_progress_payload(diagnosis_output: Any) -> dict[str, Any]:
        """从组卷链路的 Diagnosis 步骤提取学习进度，供蓝图生成确定范围。

        无进度数据时返回明确标记而非空壳，让 prompt 可以区分“尚未读取”
        与“已读取但无数据”，从而给出面向用户的明确提示而不是编造范围。
        """
        if diagnosis_output is None:
            return {"available": False, "reason": "学情诊断未执行"}
        result = getattr(diagnosis_output, "payload", None)
        if result is None:
            return {"available": False, "reason": "学情诊断结果不可用"}
        learner_data = dict(getattr(result, "learner_data", {}) or {})
        evidence_status = str(learner_data.get("evidence_status") or "unknown")
        snapshot = learner_data.get("snapshot") or {}
        available = evidence_status == "observed"
        return {
            "available": available,
            "evidence_status": evidence_status,
            "query_kind": str(learner_data.get("query_kind") or ""),
            "summary": str(getattr(result, "summary", "") or ""),
            "snapshot": snapshot,
            "reason": (
                ""
                if available
                else str(learner_data.get("reason") or "当前没有可用于组卷的学习进度数据")
            ),
        }

    @staticmethod
    def _candidate_limit(required_question_count: int) -> int:
        """System-owned retrieval capacity; never sourced from model prose."""
        return min(50, max(5, required_question_count * 2))

    @staticmethod
    def _explicit_question_count(context: dict[str, Any]) -> int | None:
        """用户明确写出的整卷题量，只能来自系统级约束通道。

        此前这里用正则从用户原话里抓第一个“数字+题”，把“10 道单选题、3 道
        多选题、2 道简答题”的总题量错读成 10（正确值是 15）。自由文本的语义
        判定由模型负责，程序只消费结构化字段。
        """

        constraints = context.get("exam_constraints", {}) or {}
        value = constraints.get("question_count")
        if isinstance(value, int) and value > 0:
            return value
        if isinstance(value, str):
            match = re.search(r"\d+", value)
            if match and int(match.group()) > 0:
                return int(match.group())
        return None

    @staticmethod
    def _explicit_duration_minutes(context: dict[str, Any]) -> int | None:
        """用户明确写出的作答时长，只能来自系统级约束通道。"""

        constraints = context.get("exam_constraints", {}) or {}
        value = constraints.get("duration_minutes")
        if isinstance(value, int) and value > 0:
            return value
        if isinstance(value, str):
            match = re.search(r"\d+", value)
            if match and int(match.group()) > 0:
                return int(match.group())
        return None

    @staticmethod
    def _explicit_question_types(context: dict[str, Any]) -> list[str]:
        """用户明确写出的题型，只能来自系统级约束通道。

        此前这里扫描用户原话里的题型别名，属于关键词匹配做业务判断。自由
        文本里的题型要求由蓝图模型理解，并由编译器从原稿提取回结构化合同。
        """

        constraints = context.get("exam_constraints", {}) or {}
        raw_types = constraints.get("question_types") or constraints.get("question_type")
        if isinstance(raw_types, str):
            raw_types = [raw_types]
        return [str(item) for item in raw_types or [] if str(item).strip()]

    @classmethod
    def _explicit_question_type_distribution(
        cls, context: dict[str, Any]
    ) -> dict[str, int]:
        constraints = context.get("exam_constraints", {}) or {}
        raw_distribution = constraints.get("question_type_distribution") or {}
        if not isinstance(raw_distribution, dict):
            return {}
        aliases = {
            "single_choice": "单项选择题",
            "singlechoice": "单项选择题",
            "单选题": "单项选择题",
            "单项选择": "单项选择题",
            "单项选择题": "单项选择题",
            "multiple_choice": "多项选择题",
            "multiplechoice": "多项选择题",
            "多选题": "多项选择题",
            "多项选择": "多项选择题",
            "多项选择题": "多项选择题",
            "true_false": "判断题",
            "truefalse": "判断题",
            "判断题": "判断题",
            "fill_blank": "填空题",
            "fillblank": "填空题",
            "填空题": "填空题",
            "short_answer": "简答题",
            "shortanswer": "简答题",
            "简答": "简答题",
            "问答题": "简答题",
            "简答题": "简答题",
            "case_quiz": "案例分析题",
            "casequiz": "案例分析题",
            "case_analysis": "案例分析题",
            "caseanalysis": "案例分析题",
            "案例题": "案例分析题",
            "病例分析题": "案例分析题",
            "临床案例题": "案例分析题",
            "案例分析题": "案例分析题",
        }
        normalized: dict[str, int] = {}
        for raw_type, raw_count in raw_distribution.items():
            key = str(raw_type).strip().replace(" ", "").replace("-", "_").lower()
            canonical = aliases.get(key)
            if canonical is None:
                continue
            try:
                count = int(raw_count)
            except (TypeError, ValueError):
                continue
            if count > 0:
                normalized[canonical] = normalized.get(canonical, 0) + count
        return normalized

    @staticmethod
    def _explicit_coverage_topics(context: dict[str, Any]) -> list[str]:
        """用户明确写出的封闭覆盖清单，只能来自系统级约束通道。

        此前这里用正则从用户原话里切“覆盖 A、B、C”，属于关键词匹配做业务
        判断：句子结构稍有不同就会切错，而且切出来的清单会整体替换掉模型
        产出的蓝图单元。自由文本的覆盖范围由蓝图模型负责。
        """

        constraints = context.get("exam_constraints", {}) or {}
        topics = constraints.get("focus_topics") or constraints.get(
            "coverage_topics"
        )
        if isinstance(topics, str):
            topics = [topics]
        return [str(item).strip() for item in topics or [] if str(item).strip()]

    @staticmethod
    def _constrain_units_to_explicit_coverage(
        units: list[dict[str, Any]], *, coverage_topics: list[str]
    ) -> list[dict[str, Any]]:
        if not coverage_topics:
            return units

        def compact(value: Any) -> str:
            return re.sub(r"[\s，、,：:；;·和及与]", "", str(value or ""))

        normalized: list[dict[str, Any]] = []
        unused = list(units)
        for topic in coverage_topics:
            topic_key = compact(topic)
            selected_index = next(
                (
                    index
                    for index, unit in enumerate(unused)
                    if topic_key in compact(
                        f"{unit.get('knowledge_module', '')}{unit.get('retrieval_query', '')}"
                    )
                    or compact(unit.get("knowledge_module")) in topic_key
                ),
                None,
            )
            if selected_index is None:
                normalized.append(
                    {
                        "knowledge_module": topic,
                        "learning_objective": f"掌握{topic}的核心内容",
                        "retrieval_query": topic,
                        "question_type_preferences": [],
                        "required_question_count": 1,
                        "candidate_limit": 10,
                        "selection_rules": [f"只选择与{topic}直接相关的题目"],
                        "target_difficulty": None,
                        "difficulty_is_hard_constraint": False,
                    }
                )
                continue
            selected = dict(unused.pop(selected_index))
            selected["knowledge_module"] = topic
            selected["retrieval_query"] = topic
            normalized.append(selected)
        return normalized

    @staticmethod
    def _normalize_question_type_mix(
        units: list[dict[str, Any]],
        *,
        explicit_types: list[str],
        has_explicit_distribution: bool = False,
    ) -> list[dict[str, Any]]:
        """把用户明确的题型要求摊到各单元。

        只有“说了题型但没给分布”时才整体覆盖，此时每个单元都可以是这些题型。
        若整卷分布本身是结构化的（例如 10 单选、3 多选、2 简答），蓝图模型已经
        按题型把题数分到各单元，再整体覆盖会把正确分布抹平——组卷阶段据此检索
        和生成，反而更难满足整卷题型门禁。
        """

        normalized = [dict(unit) for unit in units]
        if not normalized:
            return normalized
        if explicit_types:
            if has_explicit_distribution:
                return normalized
            for unit in normalized:
                unit["question_type_preferences"] = list(explicit_types)
            return normalized

        present = {
            str(question_type)
            for unit in normalized
            for question_type in unit.get("question_type_preferences", [])
        }
        choice_types = {"选择题", "单选题", "单项选择题", "多选题", "多项选择题"}
        if len(normalized) < 2 or (present and not present.issubset(choice_types)):
            return normalized

        if not present:
            normalized[0]["question_type_preferences"] = [
                "单项选择题", "多项选择题"
            ]
        normalized[-1]["question_type_preferences"] = ["案例分析题"]
        if len(normalized) >= 3:
            normalized[-2]["question_type_preferences"] = ["简答题"]
        return normalized

    @staticmethod
    def _requested_learning_scope(context: dict[str, Any]) -> dict[str, Any]:
        request = str(context.get("user_request") or "")
        match = re.search(r"第\s*([一二三四五六七八九十\d]+)\s*阶段", request)
        if not match:
            return {}
        numeral = match.group(1)
        chinese_numbers = {
            "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
            "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
        }
        stage_number = int(numeral) if numeral.isdigit() else chinese_numbers.get(numeral)
        if stage_number is None:
            return {}
        current_plan = context.get("current_long_term_plan") or {}
        stages = (
            current_plan.get("stages", [])
            if isinstance(current_plan, dict)
            else getattr(current_plan, "stages", [])
        )
        for stage in stages or []:
            value = stage if isinstance(stage, dict) else stage.model_dump()
            raw_stage = value.get("stage", value.get("order"))
            if raw_stage is None:
                stage_id = str(value.get("stage_id") or "")
                stage_match = re.search(r"(\d+)$", stage_id)
                raw_stage = int(stage_match.group(1)) if stage_match else None
            if isinstance(raw_stage, str) and raw_stage.isdigit():
                raw_stage = int(raw_stage)
            if raw_stage == stage_number:
                resolved = {
                    "requested_stage": stage_number,
                    "stage_name": value.get("name", ""),
                    "books": value.get("book") or value.get("books", []),
                    "goal": value.get("goal") or value.get("objective", ""),
                    "exit_evidence": value.get("exit_evidence", ""),
                    "source": "当前长期规划的结构化阶段",
                }
                return {key: item for key, item in resolved.items() if item != ""}
        content = PaperBlueprintAgent._plan_value(current_plan, "content")
        if content:
            chinese_numeral = next(
                (key for key, value in chinese_numbers.items() if value == stage_number),
                "",
            )
            stage_tokens = {
                token for token in (numeral, str(stage_number), chinese_numeral) if token
            }
            numeral_pattern = (
                r"第\s*(?:" + "|".join(re.escape(token) for token in stage_tokens) + r")\s*阶段"
            )
            path_match = re.search(
                numeral_pattern + r"(?P<text>[^；。\n]*(?:。|；)?)",
                content,
            )
            milestone_match = re.search(
                rf"(?:^|\n)\s*{stage_number}\s*[.、]\s*(?P<text>.*?)(?=\n\s*{stage_number + 1}\s*[.、]|\n【|$)",
                content,
                flags=re.S,
            )
            return {
                "requested_stage": stage_number,
                "resolution": "已从当前长期规划正文解析",
                "stage_description": (
                    path_match.group("text").strip(" ；。") if path_match else ""
                ),
                "stage_milestone": (
                    milestone_match.group("text").strip() if milestone_match else ""
                ),
                "source": "当前长期规划正文",
            }
        return {
            "requested_stage": stage_number,
            "resolution": "当前长期规划中未找到对应阶段",
        }

    @staticmethod
    def _plan_value(plan: Any, key: str, default: Any = "") -> Any:
        if isinstance(plan, dict):
            return plan.get(key, default)
        return getattr(plan, key, default)

    @classmethod
    def _planning_context(cls, context: dict[str, Any]) -> dict[str, Any]:
        """Keep model-relevant plan facts without leaking IDs or route internals."""
        long_term = context.get("current_long_term_plan") or {}
        short_term = context.get("current_short_term_plan") or {}
        learning_task = context.get("current_learning_task") or {}

        long_term_plan = {
            "content": cls._plan_value(long_term, "content"),
            "stages": cls._plan_value(long_term, "stages", []),
        }
        short_term_plan = {
            "content": cls._plan_value(short_term, "content"),
            "short_term_focus": cls._plan_value(short_term, "short_term_focus", {}),
            "textbook_selection": cls._plan_value(short_term, "textbook_selection", {}),
        }
        daily_task = {
            "task_content": cls._plan_value(learning_task, "task_content"),
            "estimated_minutes": cls._plan_value(learning_task, "estimated_minutes", None),
            "expected_output": cls._plan_value(learning_task, "expected_output"),
            "completion_criteria": cls._plan_value(learning_task, "completion_criteria"),
        }
        return {
            "long_term_plan": {
                key: value for key, value in long_term_plan.items() if value
            },
            "short_term_plan": {
                key: value for key, value in short_term_plan.items() if value
            },
            "daily_task": {
                key: value for key, value in daily_task.items() if value is not None and value != ""
            },
        }

    @staticmethod
    def _explicit_difficulty(context: dict[str, Any]) -> int | None:
        """用户明确写出的数字难度（1-5），只能来自系统级约束通道。

        “简单/中等/困难”等模糊词或画像中的旧难度偏好一律不推断、不默认；
        自由文本里的难度描述由蓝图模型按自己的技能规则处理。
        """

        constraints = context.get("exam_constraints", {}) or {}
        for key in ("difficulty", "target_difficulty", "difficulty_level"):
            value = constraints.get(key)
            if isinstance(value, bool):
                continue
            if isinstance(value, int) and 1 <= value <= 5:
                return value
            if isinstance(value, str):
                match = re.search(r"[1-5一二三四五]", value)
                if match:
                    return PaperBlueprintAgent._chinese_digit(match.group())
        return None

    @staticmethod
    def _chinese_digit(value: str) -> int | None:
        mapping = {
            "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
            "1": 1, "2": 2, "3": 3, "4": 4, "5": 5,
        }
        return mapping.get(str(value).strip())

    @staticmethod
    def _normalize_blueprint(raw_output: Any, context: dict[str, Any]) -> dict[str, Any]:
        raw = dict(raw_output) if isinstance(raw_output, dict) else {}
        constraints = context.get("exam_constraints", {}) or {}
        allowed_source_statuses = {
            "official",
            "user_provided_unverified",
            "practice_sample",
            "pending_confirmation",
        }
        constrained_source_status = constraints.get("source_status")
        source_status = (
            constrained_source_status
            if constrained_source_status in allowed_source_statuses
            else "user_provided_unverified"
        )
        units = raw.get("units") or raw.get("blueprint") or []
        explicit_difficulty = PaperBlueprintAgent._explicit_difficulty(context)
        normalized_units = []
        for index, item in enumerate(
            units[:20] if isinstance(units, list) else [],
            start=1,
        ):
            if not isinstance(item, dict):
                continue
            unit = dict(item)
            knowledge_module = PaperBlueprintAgent._bounded_text(
                unit.get("knowledge_module"),
                default=f"知识单元{index}",
                maximum=300,
            )
            retrieval_query = PaperBlueprintAgent._bounded_text(
                unit.get("retrieval_query")
                or unit.get("search_query")
                or knowledge_module,
                default=knowledge_module,
                maximum=300,
            )
            required_count = PaperBlueprintAgent._positive_int(
                unit.get("required_question_count"),
                default=1,
                maximum=100,
            )
            candidate_limit = PaperBlueprintAgent._positive_int(
                unit.get("candidate_limit"),
                default=max(10, required_count),
                maximum=50,
            )
            unit_target_difficulty = PaperBlueprintAgent._normalize_difficulty_value(
                unit.get("target_difficulty")
            )
            target_difficulty = (
                unit_target_difficulty
                if unit_target_difficulty is not None
                else explicit_difficulty
            )
            difficulty_is_hard_constraint = bool(
                unit.get("difficulty_is_hard_constraint")
            ) or target_difficulty is not None
            normalized_units.append({
                "knowledge_module": knowledge_module,
                "learning_objective": PaperBlueprintAgent._bounded_text(
                    unit.get("learning_objective"),
                    default="掌握该知识单元的核心内容",
                    maximum=500,
                ),
                "retrieval_query": retrieval_query,
                "question_type_preferences": PaperBlueprintAgent._string_list(
                    unit.get("question_type_preferences")
                ),
                "required_question_count": required_count,
                "score_total": PaperBlueprintAgent._positive_float(
                    unit.get(
                        "score_total",
                        unit.get("target_score", unit.get("assigned_score")),
                    )
                ),
                "candidate_limit": candidate_limit,
                "selection_rules": PaperBlueprintAgent._string_list(
                    unit.get("selection_rules") or unit.get("selection_rule")
                ),
                "assessment_dimensions": list(
                    dict.fromkeys(unit.get("assessment_dimensions") or [])
                ),
                "excluded_dimensions": list(
                    dict.fromkeys(unit.get("excluded_dimensions") or [])
                ),
                "target_difficulty": target_difficulty,
                "difficulty_is_hard_constraint": difficulty_is_hard_constraint,
            })
        assumptions = raw.get("assumptions", [])
        if isinstance(assumptions, dict):
            assumptions = [f"{key}：{value}" for key, value in assumptions.items()]
        assumptions = PaperBlueprintAgent._string_list(assumptions)
        model_source_status = raw.get("source_status")
        if (
            model_source_status
            and model_source_status not in allowed_source_statuses
        ):
            assumptions = [
                *assumptions,
                f"模型来源说明：{model_source_status}；正式来源状态由系统设为{source_status}。",
            ]
        acceptance = raw.get("acceptance_criteria") or raw.get("validation_checklist") or []
        acceptance = PaperBlueprintAgent._string_list(acceptance)
        duration = PaperBlueprintAgent._explicit_duration_minutes(context)
        model_duration = raw.get("duration_minutes") or raw.get("duration")
        if model_duration and duration is None:
            assumptions = [
                *assumptions,
                "模型建议的作答时长未得到用户或试卷约束确认，正式时长按实际题目工作量计算。",
            ]
        total_score = PaperBlueprintAgent._positive_float(
            raw.get("total_score") or constraints.get("total_score")
        )
        return {
            "title": PaperBlueprintAgent._bounded_text(
                raw.get("title"), default="章节模拟练习卷", maximum=300
            ),
            "source_status": source_status,
            "scope_summary": PaperBlueprintAgent._bounded_text(
                raw.get("scope_summary") or context.get("user_request"),
                default="围绕用户指定主题组卷",
                maximum=1_000,
            ),
            "duration_minutes": duration,
            "total_score": total_score,
            "requires_explanation": bool(raw.get("requires_explanation", False)),
            "units": normalized_units,
            "assumptions": assumptions,
            "acceptance_criteria": acceptance,
        }

    @staticmethod
    def _bounded_text(value: Any, *, default: str, maximum: int) -> str:
        text = str(value).strip() if value is not None else ""
        return (text or default)[:maximum]

    @staticmethod
    def _optional_bounded_text(value: Any, *, maximum: int) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text[:maximum] if text else None

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        if value is None:
            return []
        values = value if isinstance(value, list) else [value]
        return [str(item).strip() for item in values if str(item).strip()]

    @staticmethod
    def _positive_int(value: Any, *, default: int, maximum: int) -> int:
        try:
            parsed = int(str(value).strip().replace("题", ""))
        except (TypeError, ValueError):
            parsed = default
        return min(maximum, max(1, parsed))

    @staticmethod
    def _positive_float(value: Any) -> float | None:
        if value is None:
            return None
        try:
            parsed = float(str(value).strip().replace("分", ""))
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None

    @staticmethod
    def _normalize_difficulty_value(value: Any) -> int | None:
        """Normalize an explicit 1-5 difficulty; anything else stays None."""
        if value is None:
            return None
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value if 1 <= value <= 5 else None
        match = re.search(r"[1-5一二三四五]", str(value))
        if not match:
            return None
        return PaperBlueprintAgent._chinese_digit(match.group())

    @staticmethod
    def _with_unit_question_count(
        unit: dict[str, Any], required_question_count: int
    ) -> dict[str, Any]:
        """写回单元题数，并把检索容量提到足以覆盖它。"""

        normalized = dict(unit)
        required = max(1, required_question_count)
        normalized["required_question_count"] = required
        normalized["candidate_limit"] = min(
            50,
            max(
                int(normalized.get("candidate_limit") or 1),
                required + 2,
                required * 2,
            ),
        )
        return normalized

    @staticmethod
    def _scale_units_to_explicit_count(
        units: list[dict[str, Any]],
        explicit_count: int,
        *,
        weights: list[int] | None = None,
    ) -> list[dict[str, Any]]:
        """按权重把整卷题量分到各单元，合计严格等于整卷题量。

        每个单元至少 1 题（``BlueprintUnit.required_question_count`` 要求正
        整数），因此整卷题量小于单元数时无法逐单元分配：只保留前
        ``explicit_count`` 个单元。宁可少一个单元，也不要留下一个永远无法
        满足的题量矛盾——组卷把整卷题量和逐单元题量同时当硬约束，两者不等
        时任何组卷结果都过不了门禁，学习者会一直看到“仍有未满足的硬约束”。

        剩余题量按权重用最大余数法分配，权重缺省取各单元现有的题数，因此
        “哪个单元更重”的形状被保留；权重相等时退化为平均分配。
        """

        if not units or explicit_count < 1:
            return units
        if len(units) > explicit_count:
            logger.warning(
                "blueprint declares more units than the requested question count; "
                "keeping the leading units unit_count=%s explicit_count=%s",
                len(units),
                explicit_count,
            )
            units = units[:explicit_count]
        if weights is None:
            weights = [
                max(1, int(unit.get("required_question_count") or 1))
                for unit in units
            ]
        remaining = explicit_count - len(units)
        total_weight = sum(weights) or len(weights)
        quotas = [remaining * weight / total_weight for weight in weights]
        shares = [int(quota) for quota in quotas]
        order = sorted(
            range(len(units)),
            key=lambda index: (-(quotas[index] - shares[index]), index),
        )
        for index in order[: remaining - sum(shares)]:
            shares[index] += 1
        return [
            PaperBlueprintAgent._with_unit_question_count(unit, 1 + shares[index])
            for index, unit in enumerate(units)
        ]

    @staticmethod
    def _normalize_hard_count_units(
        units: list[dict[str, Any]],
        *,
        explicit_count: int | None,
        has_explicit_distribution: bool,
    ) -> list[dict[str, Any]]:
        """把整卷题量落实成各单元题数，并保证单元合计等于整卷题量。

        用户没有给出整卷题量时原样返回：单元题数就是模型的建议值，不是发布
        硬门槛。给出整卷题量时，单元合计必须等于它，否则整卷题量硬约束与逐
        单元题量硬约束永远无法同时满足，学习者会一直看到“仍有未满足的硬约
        束”，而且换任何一份组卷结果都消除不掉。

        分型数量存在时不做平均：蓝图模型已经按题型把题数分配到各单元，平均
        会把正确分布改坏，因此改为按模型的相对比例缩放（见
        ``_scale_units_to_explicit_count``），既保留分布形状又让合计对上。
        """

        if not units or explicit_count is None:
            return units
        if has_explicit_distribution:
            return PaperBlueprintAgent._scale_units_to_explicit_count(
                units, explicit_count
            )
        return PaperBlueprintAgent._scale_units_to_explicit_count(
            units, explicit_count, weights=[1] * len(units)
        )
