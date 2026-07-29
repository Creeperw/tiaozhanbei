"""
核心引擎模块

使用 Agent 层进行数据获取和内容生成，Agent 的具体实现在测试时注入。
"""

import json
import re
import random
import uuid
import logging
import time
from typing import Optional, List, Dict
from datetime import datetime

from .schemas import (
    SimulatedPatientRequest, SimulatedPatientResponse,
)
from .constants import PATIENT_STYLES, HELP_AFTER_TURNS, SCORE_CONFIG
from .constants import DEFAULT_LLM_TEMPERATURE, DEFAULT_LLM_MAX_TOKENS
from .constants import GRADING_LLM_TEMPERATURE, GRADING_LLM_MAX_TOKENS
from .session_manager import SessionManager
from .data_provider import DefaultDataProvider
from .case_adapter import CaseAdapter
from .agent_clients import AgentFactory
from .default_providers import DefaultLLMProvider
from .prompts import PROMPTS


logger = logging.getLogger(__name__)


class SimulatedPatientEngine:
    """模拟病患组件核心引擎 - 唯一对外接口"""

    def __init__(
        self,
        llm_provider=None,
        test_data: Dict = None,
        case_data_path: str = None,
        data_dir: str = "./simulated_patient_data"
    ):
        """
        初始化引擎

        Args:
            llm_provider: LLM 提供者（测试时传入，用于专家 Agent）
            test_data: 测试数据（测试时传入，用于记忆和学情 Agent）
            case_data_path: 案例数据路径
            data_dir: 数据存储目录
        """
        # 数据提供者（持久化）
        self.data_provider = DefaultDataProvider(
            case_data_path=case_data_path,
            data_dir=data_dir
        )

        # 如果没有传入 llm_provider，使用默认的
        if llm_provider is None:
            llm_provider = DefaultLLMProvider()

        # 创建 Agent 实例
        self.memory_agent = AgentFactory.create_memory_agent(test_data)
        self.diagnosis_agent = AgentFactory.create_diagnosis_agent(test_data)
        self.expert_agent = AgentFactory.create_expert_agent(llm_provider)
        self.knowledge_agent = AgentFactory.create_knowledge_base_agent()
        self.audit_agent = AgentFactory.create_audit_agent()

        self.session_manager = SessionManager()

    # ============================================================
    # 唯一对外接口
    # ============================================================

    def execute(self, request: SimulatedPatientRequest) -> SimulatedPatientResponse:
        """唯一对外接口 - 所有功能通过 action 参数分发"""
        if request.action == "stats":
            return self._handle_stats(request)
        if request.action == "mistakes":
            return self._handle_mistakes(request)
        if request.action == "collections":
            return self._handle_collections(request)
        if request.action == "history_detail":
            return self._handle_history_detail(request)
        if request.action == "history_list":
            return self._handle_history_list(request)
        if request.action == "reset":
            return self._handle_reset(request)
        if request.action == "dialog_history":
            return self._handle_dialog_history(request)

        session = self.session_manager.get(request.session_id)

        if request.action == "start":
            return self._handle_start(request, session)
        if request.action == "dialogue":
            return self._handle_dialogue(request, session)
        if request.action == "help":
            return self._handle_help(request, session)
        if request.action == "submit":
            return self._handle_submit(request, session)
        if request.action == "history":
            return self._handle_history(request, session)
        if request.action == "clear":
            return self._handle_clear(request, session)

        return SimulatedPatientResponse(
            session_id=request.session_id,
            action=request.action,
            success=False,
            error=f"Unknown action: {request.action}"
        )

    # ============================================================
    # 查询类处理器
    # ============================================================

    def _handle_stats(self, request: SimulatedPatientRequest) -> SimulatedPatientResponse:
        stats = self.data_provider.get_daily_stats(request.user_id)
        return SimulatedPatientResponse(
            session_id=request.session_id,
            action="stats",
            success=True,
            data={
                "total": stats.get("total", 0),
                "correct": stats.get("correct", 0),
                "wrong": stats.get("wrong", 0),
                "rate": stats.get("rate", 0),
                "streak": stats.get("streak", 0),
                "pending_review": stats.get("pending_review", 0),
                "collection_count": self.data_provider.get_collection_count(request.user_id)
            }
        )

    def _handle_mistakes(self, request: SimulatedPatientRequest) -> SimulatedPatientResponse:
        mistakes = self.data_provider.get_mistakes(request.user_id, limit=100)
        return SimulatedPatientResponse(
            session_id=request.session_id,
            action="mistakes",
            success=True,
            data={"total": len(mistakes), "list": mistakes}
        )

    def _handle_collections(self, request: SimulatedPatientRequest) -> SimulatedPatientResponse:
        collections = self.data_provider.get_collections(request.user_id)
        return SimulatedPatientResponse(
            session_id=request.session_id,
            action="collections",
            success=True,
            data={"total": len(collections), "list": collections}
        )

    def _handle_history_detail(self, request: SimulatedPatientRequest) -> SimulatedPatientResponse:
        if not request.history_id:
            return SimulatedPatientResponse(
                session_id=request.session_id,
                action="history_detail",
                success=False,
                error="Missing history_id"
            )
        record = self.data_provider.get_history_detail(request.history_id)
        if not record:
            return SimulatedPatientResponse(
                session_id=request.session_id,
                action="history_detail",
                success=False,
                error=f"History record not found: {request.history_id}"
            )
        return SimulatedPatientResponse(
            session_id=request.session_id,
            action="history_detail",
            success=True,
            data=record
        )

    def _handle_history_list(self, request: SimulatedPatientRequest) -> SimulatedPatientResponse:
        records = self.data_provider.get_all_history(request.user_id)
        return SimulatedPatientResponse(
            session_id=request.session_id,
            action="history_list",
            success=True,
            data={"total": len(records), "list": records},
        )

    def _handle_dialog_history(self, request: SimulatedPatientRequest) -> SimulatedPatientResponse:
        history = self.data_provider.get_dialog_history(request.user_id, limit=request.limit)
        return SimulatedPatientResponse(
            session_id=request.session_id,
            action="dialog_history",
            success=True,
            data={"total": len(history), "list": history}
        )

    # ============================================================
    # 重置
    # ============================================================

    def _handle_reset(self, request: SimulatedPatientRequest) -> SimulatedPatientResponse:
        session = self.session_manager.get(request.session_id)
        if session.get("status") == "init":
            return SimulatedPatientResponse(
                session_id=request.session_id,
                action="reset",
                success=False,
                error="Session not initialized"
            )
        if "case" not in session:
            return SimulatedPatientResponse(
                session_id=request.session_id,
                action="reset",
                success=False,
                error="No case data found"
            )

        case = session["case"]
        style = session["style"]
        basic_info = session["basic_info"]

        session["turn_count"] = 0
        session["help_available"] = False
        session["help_used_count"] = 0
        session["dialog_history"] = []
        session["messages"] = self._build_system_messages(case, basic_info, style)
        session["status"] = "active"

        opening = self._generate_opening(session["messages"], style, case, basic_info)
        session["messages"].append({"role": "assistant", "content": opening})
        session["turn_count"] = 1
        session["dialog_history"].append({"role": "patient", "content": opening})
        self.session_manager.set(request.session_id, session)

        self.data_provider.save_dialog_entry(request.user_id, {
            "session_id": request.session_id,
            "turn": 1,
            "role": "patient",
            "content": opening,
            "timestamp": datetime.now().isoformat()
        })

        return SimulatedPatientResponse(
            session_id=request.session_id,
            action="reset",
            success=True,
            data={
                "patient_reply": opening,
                "patient_info": {
                    "gender": basic_info["gender"],
                    "age_range": basic_info["age_range"],
                    "body_type": basic_info["body_type"]
                },
                "message": "已重置，重新开始问诊"
            },
            turn_count=session["turn_count"],
            help_available=False
        )

    # ============================================================
    # 开始问诊
    # ============================================================

    def _handle_start(self, request: SimulatedPatientRequest, session: Dict) -> SimulatedPatientResponse:
        """开始问诊"""
        # 如果没有传 case_id，自动从测试数据注入画像并筛选案例
        if not request.case_id:
            # 从 Agent 获取记忆管理和学情分析的数据
            learner_context = self.memory_agent.get_learner_context(request.user_id)
            diagnosis_result = self.diagnosis_agent.get_diagnosis_result(request.user_id)
            session['learner_context'] = learner_context
            session['diagnosis_result'] = diagnosis_result

            # 提取薄弱知识点用于案例筛选
            weak_kps = diagnosis_result.get('payload', {}).get('weak_kp_ids', [])
            cases = self.data_provider.get_cases_for_user(weak_kps)

            if not cases:
                return SimulatedPatientResponse(
                    session_id=request.session_id,
                    action="start",
                    success=False,
                    error="No suitable case found for this user"
                )
            case = random.choice(cases)
        else:
            case = self.data_provider.get_case_by_id(request.case_id)
            if not case:
                return SimulatedPatientResponse(
                    session_id=request.session_id,
                    action="start",
                    success=False,
                    error=f"Case not found: {request.case_id}"
                )
            if request.learner_context:
                session['learner_context'] = request.learner_context
            if request.diagnosis_result:
                session['diagnosis_result'] = request.diagnosis_result

        # 适配案例
        adapted_case = CaseAdapter.adapt(case)
        if not adapted_case:
            return SimulatedPatientResponse(
                session_id=request.session_id,
                action="start",
                success=False,
                error="Failed to adapt case"
            )

        # 提取用户画像（从 session 或 request）
        user_profile = {}
        if request.learner_context:
            user_profile = self._extract_user_profile(request.learner_context)
        elif 'learner_context' in session:
            user_profile = self._extract_user_profile(session['learner_context'])

        # 提取学情状态
        learning_state = {}
        if request.diagnosis_result:
            learning_state = self._extract_learning_state(request.diagnosis_result)
        elif 'diagnosis_result' in session:
            learning_state = self._extract_learning_state(session['diagnosis_result'])

        # 选择患者风格
        style = random.choice(PATIENT_STYLES)
        basic_info = self._generate_basic_info(adapted_case)

        session["case"] = adapted_case
        session["raw_case"] = case
        session["style"] = style
        session["basic_info"] = basic_info
        session["user_profile"] = user_profile
        session["learning_state"] = learning_state
        session["turn_count"] = 0
        session["help_available"] = False
        session["help_used_count"] = 0
        session["dialog_history"] = []
        session["messages"] = self._build_system_messages(adapted_case, basic_info, style)
        session["status"] = "active"

        opening = self._generate_opening(session["messages"], style, adapted_case, basic_info)
        session["messages"].append({"role": "assistant", "content": opening})
        session["turn_count"] = 1
        session["dialog_history"].append({"role": "patient", "content": opening})
        self.session_manager.set(request.session_id, session)

        # 保存到持久化
        self.data_provider.save_dialog_entry(request.user_id, {
            "session_id": request.session_id,
            "turn": 1,
            "role": "patient",
            "content": opening,
            "timestamp": datetime.now().isoformat()
        })

        return SimulatedPatientResponse(
            session_id=request.session_id,
            action="start",
            success=True,
            data={
                "patient_reply": opening,
                "patient_info": {
                    "gender": basic_info["gender"],
                    "age_range": basic_info["age_range"],
                    "body_type": basic_info["body_type"]
                },
                "case_name": adapted_case.get("name", adapted_case.get("syndrome", "")),
                "user_profile": user_profile,
                "learning_state": learning_state
            },
            turn_count=session["turn_count"]
        )

    # ============================================================
    # 对话（已修复 help_available 触发时机）
    # ============================================================

    def _handle_dialogue(self, request: SimulatedPatientRequest, session: Dict) -> SimulatedPatientResponse:
        if session.get("status") == "init":
            return SimulatedPatientResponse(
                session_id=request.session_id,
                action="dialogue",
                success=False,
                error="Session not initialized"
            )
        if session.get("status") == "completed":
            return SimulatedPatientResponse(
                session_id=request.session_id,
                action="dialogue",
                success=False,
                error="Round ended"
            )
        if not request.user_input:
            return SimulatedPatientResponse(
                session_id=request.session_id,
                action="dialogue",
                success=False,
                error="Please enter question"
            )

        messages = session["messages"]
        turn_count = session["turn_count"]
        turn_count += 1
        session["turn_count"] = turn_count

        # 在轮次增加后检查是否达到援助触发条件
        if turn_count >= HELP_AFTER_TURNS:
            session["help_available"] = True

        messages.append({"role": "user", "content": request.user_input})
        session["dialog_history"].append({"role": "doctor", "content": request.user_input})

        self.data_provider.save_dialog_entry(request.user_id, {
            "session_id": request.session_id,
            "turn": turn_count,
            "role": "doctor",
            "content": request.user_input,
            "timestamp": datetime.now().isoformat()
        })

        reply = self.expert_agent.generate_patient_reply(
            messages,
            session["style"],
            session["case"],
            session["basic_info"]
        )

        if reply:
            messages.append({"role": "assistant", "content": reply})
            session["dialog_history"].append({"role": "patient", "content": reply})
            self.data_provider.save_dialog_entry(request.user_id, {
                "session_id": request.session_id,
                "turn": turn_count,
                "role": "patient",
                "content": reply,
                "timestamp": datetime.now().isoformat()
            })
        else:
            messages.pop()
            return SimulatedPatientResponse(
                session_id=request.session_id,
                action="dialogue",
                success=False,
                error="Patient communication failed"
            )

        self.session_manager.set(request.session_id, session)
        return SimulatedPatientResponse(
            session_id=request.session_id,
            action="dialogue",
            success=True,
            data={"patient_reply": reply},
            turn_count=turn_count,
            help_available=session.get("help_available", False)
        )

    # ============================================================
    # 帮助
    # ============================================================

    def _handle_help(self, request: SimulatedPatientRequest, session: Dict) -> SimulatedPatientResponse:
        if not session.get("help_available"):
            return SimulatedPatientResponse(
                session_id=request.session_id,
                action="help",
                success=False,
                error=f"Help available after {HELP_AFTER_TURNS} turns"
            )

        if request.help_type not in ["question", "interpretation"]:
            return SimulatedPatientResponse(
                session_id=request.session_id,
                action="help",
                success=False,
                error="Choose question or interpretation"
            )

        case = session["case"]
        history = session.get("dialog_history", [])

        result = self.expert_agent.generate_help(request.help_type, case, history)

        return SimulatedPatientResponse(
            session_id=request.session_id,
            action="help",
            success=True,
            data=result,
            turn_count=session["turn_count"],
            help_available=True
        )

    # ============================================================
    # 提交诊断
    # ============================================================

    def _handle_submit(self, request: SimulatedPatientRequest, session: Dict) -> SimulatedPatientResponse:
        if not request.diagnosis:
            return SimulatedPatientResponse(
                session_id=request.session_id,
                action="submit",
                success=False,
                error="Please provide diagnosis"
            )

        case = session["case"]
        diagnosis = request.diagnosis
        practice_scope = request.practice_scope

        learner_context = session.get('learner_context', {})
        diagnosis_result = session.get('diagnosis_result', {})

        query = case.get('symptoms', '') + ' ' + case.get('signs', '')
        evidence_pack = self.knowledge_agent.build_evidence_pack(query, session.get('raw_case', {}))

        grading_report = self.expert_agent.grade_answer(
            case, diagnosis, learner_context, diagnosis_result, evidence_pack
        )

        # 根据实际对话轮数修正时间效率分数（LLM 无法准确判断轮数）
        turn_count = session.get("turn_count", 0)
        if turn_count <= 15:
            time_score = 8
        elif turn_count <= 25:
            time_score = 6
        else:
            time_score = 3
        breakdown = grading_report.get("score_breakdown", {})
        if isinstance(breakdown, dict):
            breakdown["time_efficiency_score"] = time_score
            # 重新计算总分
            score_sum = sum(
                v for k, v in breakdown.items()
                if k.endswith("_score") and isinstance(v, (int, float))
            )
            grading_report["score"] = score_sum
            # 修正评分理由
            reasons = breakdown.get("score_reasons", {})
            if isinstance(reasons, dict):
                reasons["time_efficiency"] = f"实际问诊{turn_count}轮（≤15轮满分，16-25轮得6分，>25轮得3分）"

        audit_result = self.audit_agent.review_content(
            {"artifact_id": "DRAFT_001", "payload": grading_report},
            evidence_pack
        )

        kp_list = []
        knowledge = case.get('knowledge', {})
        for kp_id, kp_data in knowledge.items():
            kp_list.append({
                "kp_id": kp_id,
                "kp_name": kp_data.get('name', ''),
                "kp_description": kp_data.get('description', '')
            })

        history_id = f"HIST_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:4]}"

        history_record = {
            "history_id": history_id,
            "user_id": request.user_id,
            "session_id": request.session_id,
            "case_id": case.get('id', ''),
            "case_name": case.get('name', case.get('syndrome', '')),
            "department": case.get('department', ''),
            "date": datetime.now().strftime("%Y-%m-%d"),
            "time": datetime.now().strftime("%H:%M:%S"),
            "timestamp": datetime.now().isoformat(),
            "turn_count": session.get("turn_count", 0),
            "gender": session.get("basic_info", {}).get("gender", ""),
            "age_range": session.get("basic_info", {}).get("age_range", ""),
            "body_type": session.get("basic_info", {}).get("body_type", ""),
            "score": grading_report.get('score', 0),
            "diagnosis_correct": grading_report.get('diagnosis_correct', False),
            "user_syndrome": diagnosis.get('syndrome', ''),
            "correct_syndrome": case.get('syndrome', ''),
            "practice_scope": practice_scope,
            "full_dialogue": session.get("dialog_history", []),
            "grading_report": grading_report,
            "kp_ids": [kp.get('kp_id') for kp in kp_list],
            "kp_names": [kp.get('kp_name') for kp in kp_list],
            "user_comment": diagnosis.get('notes', '')
        }

        self.data_provider.save_history_record(request.user_id, history_record)

        if not grading_report.get('diagnosis_correct', False):
            self.data_provider.add_mistake_record(
                request.user_id, case.get('id', ''), diagnosis, grading_report,
                history_id=history_id, session_id=request.session_id,
            )

        self.data_provider.record_stats(
            request.user_id,
            case.get('id', ''),
            grading_report.get('diagnosis_correct', False),
            grading_report.get('score', 0),
            practice_scope
        )

        writeback_intents = []
        if not grading_report.get('diagnosis_correct', False):
            writeback_intents.append({
                "intent_id": f"WBI_{uuid.uuid4().hex[:8]}",
                "effect_type": "upsert_mistake_record",
                "target_service": "learning_analytics_service",
                "payload": {"history_id": history_id},
                "status": "completed"
            })

        writeback_intents.append({
            "intent_id": f"WBI_{uuid.uuid4().hex[:8]}",
            "effect_type": "record_stats",
            "target_service": "learning_analytics_service",
            "payload": {},
            "status": "completed"
        })

        session["status"] = "completed"
        self.session_manager.set(request.session_id, session)

        grading_report["history_id"] = history_id
        grading_report["knowledge_points"] = kp_list
        grading_report["audit_result"] = audit_result

        return SimulatedPatientResponse(
            session_id=request.session_id,
            action="submit",
            success=True,
            data={"grading_report": grading_report, "history_id": history_id},
            turn_count=session["turn_count"],
            is_complete=True,
            writeback_intents=writeback_intents
        )

    # ============================================================
    # 历史和清除
    # ============================================================

    def _handle_history(self, request: SimulatedPatientRequest, session: Dict) -> SimulatedPatientResponse:
        history = session.get("dialog_history", [])
        return SimulatedPatientResponse(
            session_id=request.session_id,
            action="history",
            success=True,
            data={"total": len(history), "list": history},
            turn_count=session.get("turn_count", 0)
        )

    def _handle_clear(self, request: SimulatedPatientRequest, session: Dict) -> SimulatedPatientResponse:
        self.session_manager.delete(request.session_id)
        return SimulatedPatientResponse(
            session_id=request.session_id,
            action="clear",
            success=True,
            data={"message": "Session cleared"}
        )

    # ============================================================
    # 辅助方法
    # ============================================================

    def _generate_basic_info(self, case: Dict) -> Dict:
        """生成患者基本信息"""
        gender_preference = case.get("gender_preference", "随机")
        gender = "女" if gender_preference == "女" else "男" if gender_preference == "男" else random.choice(["男", "女"])
        age_range = case.get("age_range", "中年（35-55岁）")
        symptoms = case.get("symptoms", "")

        # 提取年龄数值
        age_match = re.search(r'(\d+)', age_range)
        age = int(age_match.group(1)) if age_match else 40

        thin_kw = ["食欲不振", "食后腹胀", "神疲乏力", "面色萎黄", "大便溏薄"]
        fat_kw = ["肥胖", "痰多", "苔腻", "带下量多", "湿热"]
        thin_score = sum(1 for kw in thin_kw if kw in symptoms)
        fat_score = sum(1 for kw in fat_kw if kw in symptoms)
        body_type = "偏瘦" if thin_score > fat_score else "偏胖" if fat_score > thin_score else "适中"

        return {
            "gender": gender,
            "age_range": age_range,
            "age": age,
            "body_type": body_type
        }

    # ============================================================
    # 修改点：_build_system_messages - 增加脉象提取和传递
    # ============================================================

    def _build_system_messages(self, case: Dict, basic_info: Dict, style: Dict) -> List[Dict]:
        """构建系统消息 - 传递症状和脉象信息"""
        symptoms = case.get('symptoms', '无特殊不适')
        signs = case.get('signs', '')

        # 从体征中提取脉象信息
        pulse = ""
        if "脉" in signs:
            pulse_match = re.search(r'脉[：:]\s*([^，,。；;]+)', signs)
            if pulse_match:
                pulse = pulse_match.group(1).strip()
            else:
                pulse_match = re.search(r'([\u4e00-\u9fa5]+脉)', signs)
                if pulse_match:
                    pulse = pulse_match.group(1).strip()

        # 构建病情信息
        condition_info = f"主诉：{symptoms}"
        if pulse:
            condition_info += f"\n你的脉象是：{pulse}（但不要主动说出，只有医生问脉象时才以旁白形式回答）"

        from .prompts import PROMPTS, PATIENT_CORE_RULES

        system_prompt = PROMPTS["patient_system"].format(
            age=basic_info.get('age', 45),
            gender=basic_info.get('gender', '男'),
            body_type=basic_info.get('body_type', '适中'),
            style_name=style["name"],
            style_traits=style["traits"],
            condition_info=condition_info,
            PATIENT_CORE_RULES=PATIENT_CORE_RULES
        )

        return [{"role": "system", "content": system_prompt}]

    def _generate_opening(self, messages: List[Dict], style: Dict, case: Dict, basic_info: Dict) -> str:
        """生成开场白"""
        reply = self.expert_agent.generate_patient_reply(messages, style, case, basic_info)
        if reply:
            return reply
        return random.choice(style["opening_templates"])

    def _extract_user_profile(self, learner_context: Dict) -> Dict:
        """从 LearnerContext 提取用户画像"""
        payload = learner_context.get('payload', {})
        stable_profile = payload.get('stable_profile', {})
        return {
            "user_id": payload.get('user_id', ''),
            "user_group": stable_profile.get('user_group', '未知'),
            "daily_available_minutes": stable_profile.get('daily_available_minutes', 0),
            "preferences": payload.get('confirmed_preferences', {}),
            "relevant_memories": payload.get('relevant_memories', []),
            "temporary_constraints": payload.get('temporary_constraints', [])
        }

    def _extract_learning_state(self, diagnosis_result: Dict) -> Dict:
        """从 DiagnosisResult 提取学情状态"""
        payload = diagnosis_result.get('payload', {})
        return {
            "stage_id": payload.get('stage_id', 'T0'),
            "stage_name": payload.get('stage_name', '稳定学习'),
            "severity": payload.get('severity', 'low'),
            "confidence": payload.get('confidence', 0.0),
            "weak_kp_ids": payload.get('weak_kp_ids', []),
            "primary_attribution": payload.get('primary_attribution', ''),
            "suggested_action": payload.get('suggested_action', {}),
            "evidence": payload.get('evidence', [])
        }

    def _call_llm(self, messages, temperature=DEFAULT_LLM_TEMPERATURE, max_tokens=DEFAULT_LLM_MAX_TOKENS, max_retries=3):
        if not self.llm_provider:
            print("❌ llm_provider 为 None")
            return None
        for attempt in range(max_retries):
            try:
                result = self.llm_provider.chat(messages, temperature, max_tokens)
                if result is None:
                    print(f"❌ llm_provider.chat 返回 None (尝试 {attempt+1}/{max_retries})")
                return result
            except Exception as e:
                print(f"❌ LLM调用失败 (尝试 {attempt+1}/{max_retries}): {e}")
                wait_time = 2 ** attempt
                if attempt < max_retries - 1:
                    time.sleep(wait_time)
                else:
                    print("❌ LLM调用最终失败")
                    return None
        return None

    def _format_history(self, messages: List[Dict]) -> str:
        lines = []
        for msg in messages:
            if msg["role"] == "system":
                continue
            lines.append(f"{'医生' if msg['role'] == 'user' else '患者'}：{msg['content']}")
        return "\n".join(lines)
