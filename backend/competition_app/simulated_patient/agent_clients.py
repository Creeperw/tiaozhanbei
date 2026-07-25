"""
Agent 调用层 - 定义所有 Agent 的接口，提供测试环境实现

本文件是组件与外部 Agent 系统的交互边界。
- 测试环境：使用千问模型替代专家 Agent，使用测试数据替代其他 Agent
- 生产环境：替换为真实的 Agent 调用
"""

import json
import random
import uuid
from typing import Dict, List, Optional
from abc import ABC, abstractmethod

from .prompts import PROMPTS


# ==================== Agent 接口定义 ====================

class MemoryAgent(ABC):
    """记忆管理 Agent 接口"""
    
    @abstractmethod
    def get_learner_context(self, user_id: str) -> Dict:
        """获取学习者上下文（符合 记忆管理_output.md）"""
        pass


class DiagnosisAgent(ABC):
    """学情分析 Agent 接口"""
    
    @abstractmethod
    def get_diagnosis_result(self, user_id: str) -> Dict:
        """获取学情诊断结果（符合 学情分析_output.md）"""
        pass


class KnowledgeBaseAgent(ABC):
    """知识库管理 Agent 接口"""
    
    @abstractmethod
    def build_evidence_pack(self, query: str, case: Dict) -> Dict:
        """构建证据包（符合 知识库管理_output.md）"""
        pass


class ExpertAgent(ABC):
    """专家 Agent 接口"""
    
    @abstractmethod
    def grade_answer(self, case: Dict, diagnosis: Dict, learner_context: Dict,
                     diagnosis_result: Dict, evidence_pack: Dict) -> Dict:
        """批改答案（符合 专家_output.md）"""
        pass
    
    @abstractmethod
    def generate_patient_reply(self, messages: List[Dict], style: Dict, 
                               case: Dict, basic_info: Dict) -> str:
        """生成患者回复"""
        pass
    
    @abstractmethod
    def generate_help(self, help_type: str, case: Dict, history: List[Dict]) -> Dict:
        """生成帮助内容（关键问题或症状解读）"""
        pass


class AuditAgent(ABC):
    """审核裁判 Agent 接口"""
    
    @abstractmethod
    def review_content(self, draft: Dict, evidence_pack: Dict) -> Dict:
        """审核内容（符合 审核裁判_output.md）"""
        pass


# ==================== 测试环境实现 ====================

class TestMemoryAgent(MemoryAgent):
    """测试环境：从 test_component_data.json 读取数据"""
    
    def __init__(self, test_data: Dict):
        self.test_data = test_data
    
    def get_learner_context(self, user_id: str) -> Dict:
        raw = self.test_data.get(user_id, {}).get('user_profile', {})
        if not raw:
            return {
                "artifact_id": f"LC_{user_id}",
                "artifact_type": "learner_context",
                "trace_id": f"TRACE_{uuid.uuid4().hex[:8]}",
                "producer": "memory_agent",
                "version": 1,
                "payload": {
                    "user_id": user_id,
                    "stable_profile": {"user_group": "", "daily_available_minutes": 30},
                    "confirmed_preferences": {},
                    "relevant_memories": [],
                    "temporary_constraints": [],
                    "memory_conflicts": [],
                    "profile_update_suggestions": []
                },
                "status": "success",
                "schema_version": "1.0.0"
            }
        
        # 从 question_attempt 提取错题作为相关记忆
        relevant_memories = []
        attempts = self.test_data.get(user_id, {}).get('question_attempt', [])
        for att in attempts:
            if not att.get('is_correct', True):
                kp_ids = self._get_kp_ids_for_question(att.get('question_id'))
                relevant_memories.append({
                    "memory_id": f"MEM_{att.get('attempt_id', uuid.uuid4().hex[:8])}",
                    "summary": f"在题目 {att.get('question_id')} 上犯错，错因：{att.get('reason_for_mistake', '未知')}",
                    "kp_refs": kp_ids,
                    "source_refs": [att.get('attempt_id')]
                })
        
        return {
            "artifact_id": f"LC_{user_id}",
            "artifact_type": "learner_context",
            "trace_id": f"TRACE_{uuid.uuid4().hex[:8]}",
            "producer": "memory_agent",
            "version": 1,
            "payload": {
                "user_id": user_id,
                "stable_profile": {
                    "user_group": raw.get('user_group', {}).get('group1', ''),
                    "daily_available_minutes": raw.get('daily_available_minutes', 30)
                },
                "confirmed_preferences": raw.get('user_preference', {}),
                "relevant_memories": relevant_memories,
                "temporary_constraints": ["本次可学习15分钟"],
                "memory_conflicts": [],
                "profile_update_suggestions": []
            },
            "status": "success",
            "schema_version": "1.0.0"
        }
    
    def _get_kp_ids_for_question(self, question_id: str) -> List[str]:
        for uid, tc in self.test_data.items():
            for q in tc.get('question', []):
                if q.get('question_id') == question_id:
                    return q.get('kp_ids', [])
        return []


class TestDiagnosisAgent(DiagnosisAgent):
    """测试环境：从 test_component_data.json 读取数据"""
    
    def __init__(self, test_data: Dict):
        self.test_data = test_data
    
    def get_diagnosis_result(self, user_id: str) -> Dict:
        raw_lp = self.test_data.get(user_id, {}).get('learning_profile', {})
        status = raw_lp.get('current_status', {})
        weak_kps = self._extract_weak_kps(user_id)
        
        stage_id = status.get('status_code', 'T0')
        stage_name = status.get('status_name', '稳定学习')
        
        all_stages = ['T0', 'T1', 'T2', 'T3', 'T4', 'T5', 'T6']
        stage_matches = []
        for s in all_stages:
            if s == stage_id:
                stage_matches.append({
                    "stage_id": s,
                    "matched": True,
                    "evidence": status.get('evidence', [])
                })
            else:
                stage_matches.append({
                    "stage_id": s,
                    "matched": False,
                    "evidence": ["未触发该阶段规则"]
                })
        
        confidence = status.get('confidence', 0.5)
        severity = "low" if confidence > 0.7 else "medium"
        
        attempts = self.test_data.get(user_id, {}).get('question_attempt', [])
        wrong_count = sum(1 for a in attempts if not a.get('is_correct', True))
        
        return {
            "artifact_id": f"DIA_{user_id}",
            "artifact_type": "diagnosis_result",
            "trace_id": f"TRACE_{uuid.uuid4().hex[:8]}",
            "producer": "diagnosis_agent",
            "version": 1,
            "payload": {
                "learner_id": user_id,
                "rule_set_ref": {
                    "rule_set_id": "LEARNING_DIAGNOSIS_RULESET_001",
                    "version": 1,
                    "source": "teaching_expert_confirmed"
                },
                "stage_id": stage_id,
                "stage_name": stage_name,
                "secondary_stage_ids": [],
                "stage_matches": stage_matches,
                "severity": severity,
                "confidence": confidence,
                "weak_kp_ids": weak_kps,
                "primary_attribution": status.get('primary_attribution', '前置概念混淆'),
                "evidence": [
                    {
                        "metric": "retry_count",
                        "value": wrong_count,
                        "baseline": 2,
                        "supports": stage_id
                    }
                ],
                "suggested_action": {
                    "action": "add_prerequisite_comparison_card",
                    "detail": "先补证候与方剂适用边界，再做基础题"
                },
                "follow_up": {
                    "check_after": ["T+24h"],
                    "success_condition": "基础题正确率达到0.8"
                }
            },
            "status": "success",
            "schema_version": "1.0.0"
        }
    
    def _extract_weak_kps(self, user_id: str) -> List[str]:
        attempts = self.test_data.get(user_id, {}).get('question_attempt', [])
        weak = set()
        for att in attempts:
            if not att.get('is_correct', True):
                q_id = att.get('question_id')
                for uid, tc in self.test_data.items():
                    for q in tc.get('question', []):
                        if q.get('question_id') == q_id:
                            weak.update(q.get('kp_ids', []))
        return list(weak)


class TestExpertAgent(ExpertAgent):
    """测试环境：使用千问模型作为专家 Agent"""
    
    def __init__(self, llm_provider):
        self.llm_provider = llm_provider
    
    def grade_answer(self, case: Dict, diagnosis: Dict, learner_context: Dict,
                     diagnosis_result: Dict, evidence_pack: Dict) -> Dict:
        """使用千问模型批改答案"""
        prompt = self._build_grading_prompt(case, diagnosis, learner_context, 
                                            diagnosis_result, evidence_pack)
        response = self.llm_provider.chat(
            [{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=2500
        )
        
        try:
            expert_output = json.loads(response) if response else {}
        except:
            expert_output = {}
        
        # 提取评分信息
        payload = expert_output.get('payload', {})
        content = payload.get('content', {})
        
        return {
            "score": content.get('score', 0),
            "score_breakdown": content.get('score_breakdown', {}),
            "correct_answer": content.get('correct_answer', {}),
            "diagnosis_correct": content.get('diagnosis_correct', False),
            "error_reason": content.get('error_reason', ''),
            "syndrome_comparison": content.get('syndrome_comparison', {}),
            "inquiry_analysis": content.get('inquiry_analysis', {}),
            "knowledge_analysis": content.get('knowledge_analysis', {}),
            "error_analysis": content.get('error_analysis', {}),
            "expert_artifact_id": expert_output.get('artifact_id')
        }
    
    def _build_grading_prompt(self, case: Dict, diagnosis: Dict, learner_context: Dict,
                              diagnosis_result: Dict, evidence_pack: Dict) -> str:
        # 从 learner_context 提取信息
        payload = learner_context.get('payload', {})
        stable_profile = payload.get('stable_profile', {})
        
        # 从 diagnosis_result 提取信息
        diag_payload = diagnosis_result.get('payload', {})
        stage_id = diag_payload.get('stage_id', 'T0')
        weak_kps = diag_payload.get('weak_kp_ids', [])
        
        # 从 evidence_pack 提取金标准
        ep_payload = evidence_pack.get('payload', {})
        evidence_items = ep_payload.get('evidence_items', [])
        correct_answer = evidence_items[0].get('content', '') if evidence_items else case.get('题目答案', '')
        
        # 构建对话历史摘要
        dialog_history = case.get('_dialog_history', [])
        history_str = "\n".join([f"{'医' if h['role']=='doctor' else '患'}：{h['content']}" for h in dialog_history[-15:]])
        
        # 从案例提取金标准
        syndrome = case.get('syndrome', '')
        key_points = "、".join(case.get('key_points', []))
        
        prompt = f"""你是一位资深中医/临床专家，负责批改学员的诊断。

【案例金标准】
- 诊断/证型：{syndrome}
- 辨证要点：{key_points}
- 标准答案参考：{correct_answer[:200]}

【学员提交的答案】
- 诊断/证型：{diagnosis.get('syndrome', '未填')}
- 方剂名：{diagnosis.get('prescription', '未填')}
- 组成：{diagnosis.get('composition', '未填')}
- 注意事项：{diagnosis.get('notes', '未填')}

【学员学习背景】
- 用户群体：{stable_profile.get('user_group', '未知')}
- 当前学习阶段：{stage_id}
- 薄弱知识点：{', '.join(weak_kps) if weak_kps else '无'}

【问诊对话历史（最近15轮）】
{history_str if history_str else '无对话记录'}

【评分规则】（六维评分，满分100分）
1. 证型/诊断判断（满分40分）：完全正确得40分，部分正确得20分，错误得0分
2. 方剂名称（满分15分）：完全正确得15分，相近得7分，错误得0分
3. 方剂组成（满分15分）：完全正确得15分，错1-2味得7分，错3味以上得0分
4. 问诊内容-望闻问切（满分15分）：四诊覆盖全面得15分，覆盖大部分得10分，覆盖少部分得5分
5. 问诊时间与效率（满分8分）：高效(≤15轮)得8分，正常(16-25轮)得6分，低效(>25轮)得3分
6. 人文关怀（满分7分）：优秀得7分，良好得5分，一般得3分

请按以下JSON格式输出评分报告：
{{
    "artifact_id": "DRAFT_xxx",
    "artifact_type": "content_draft",
    "producer": "expert_agent",
    "version": 1,
    "payload": {{
        "resource_type": "grading_feedback",
        "content": {{
            "score": 总分(0-100),
            "score_breakdown": {{
                "syndrome_score": 0-40,
                "syndrome_max": 40,
                "prescription_name_score": 0-15,
                "prescription_name_max": 15,
                "prescription_comp_score": 0-15,
                "prescription_comp_max": 15,
                "inquiry_score": 0-15,
                "inquiry_max": 15,
                "time_efficiency_score": 0-8,
                "time_efficiency_max": 8,
                "compassion_score": 0-7,
                "compassion_max": 7,
                "score_reasons": {{
                    "syndrome": "评分理由",
                    "prescription_name": "评分理由",
                    "prescription_comp": "评分理由",
                    "inquiry": "评分理由",
                    "time_efficiency": "评分理由",
                    "compassion": "评分理由"
                }}
            }},
            "correct_answer": {{
                "syndrome": "正确证型",
                "prescription_name": "正确方剂",
                "prescription_composition": "正确组成",
                "special_notes": "注意事项"
            }},
            "diagnosis_correct": true/false,
            "syndrome_comparison": {{
                "user_syndrome": "用户填的证型",
                "correct_syndrome": "正确证型",
                "comparison": "对比分析"
            }},
            "inquiry_analysis": {{
                "good_points": ["好点1", "好点2"],
                "improvement_points": ["改进点1", "改进点2"],
                "compassion_analysis": "人文关怀分析",
                "time_analysis": "时间效率分析"
            }},
            "knowledge_analysis": {{
                "key_points": ["辨证要点1", "辨证要点2"],
                "details": [{{"name": "知识点名", "description": "描述"}}]
            }},
            "error_analysis": {{
                "suggestion": "综合建议",
                "review_schedule": "复习安排"
            }},
            "error_reason": "错因描述"
        }},
        "claims": [],
        "personalization_notes": ["个性化调整说明"],
        "safety_notes": ["仅作教学"]
    }},
    "status": "pending_review",
    "schema_version": "1.0.0"
}}"""
        return prompt
    
    def generate_patient_reply(self, messages: List[Dict], style: Dict,
                               case: Dict, basic_info: Dict) -> str:
        """使用千问模型生成患者回复"""
        # 构建系统提示
        symptoms = case.get('symptoms', '')
        signs = case.get('signs', '')
        age_range = basic_info.get('age_range', '中年')
        gender = basic_info.get('gender', '男')
        body_type = basic_info.get('body_type', '适中')
        
        # 提取年龄数值
        import re
        age_match = re.search(r'(\d+)', age_range)
        age = int(age_match.group(1)) if age_match else 40
        
        system_prompt = PROMPTS["patient_system"].format(
            age=age,
            gender=gender,
            body_type=body_type,
            style_name=style.get('name', '温和配合型'),
            style_traits=style.get('traits', '礼貌、配合'),
            condition_info=f"症状：{symptoms}\n体征：{signs}",
            PATIENT_CORE_RULES=PROMPTS["patient_core_rules"]
        )
        
        # 构建对话历史（最近10轮）
        history = "\n".join([
            f"{'医' if m['role']=='user' else '患'}：{m['content']}" 
            for m in messages[-10:] if m['role'] != 'system'
        ])
        
        user_prompt = f"""请根据以下对话历史，以患者身份回复医生的最后一句话。

对话历史：
{history}

请以患者口吻回复，简短自然，符合你的性格特征。只输出回复内容。"""
        
        full_messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
        
        response = self.llm_provider.chat(full_messages, temperature=0.7, max_tokens=200)
        
        if response:
            return response
        # 降级：使用模板
        return random.choice(style.get("opening_templates", ["请描述您的症状。"]))

    def generate_help(self, help_type: str, case: Dict, history: List[Dict]) -> Dict:
        """使用千问模型生成帮助内容"""
        syndrome = case.get('syndrome', '')
        symptoms = case.get('symptoms', '')
        history_str = "\n".join([f"{'医' if h['role']=='doctor' else '患'}：{h['content']}" for h in history[-10:]])
        
        if help_type == "question":
            prompt = PROMPTS["help_question"].format(
                syndrome=syndrome,
                symptoms=symptoms,
                conversation_history=history_str
            )
            response = self.llm_provider.chat(
                [{"role": "user", "content": prompt}],
                temperature=0.5,
                max_tokens=300
            )
            return {"question": response or "无法生成问题"}
        else:
            prompt = PROMPTS["help_interpretation"].format(
                syndrome=syndrome,
                symptoms=symptoms,
                conversation_history=history_str
            )
            response = self.llm_provider.chat(
                [{"role": "user", "content": prompt}],
                temperature=0.5,
                max_tokens=500
            )
            return {"interpretation": response or "无法生成解读"}


class TestKnowledgeBaseAgent(KnowledgeBaseAgent):
    """测试环境：从案例中构建证据包"""
    
    def build_evidence_pack(self, query: str, case: Dict) -> Dict:
        return {
            "artifact_id": f"EP_{uuid.uuid4().hex[:8]}",
            "artifact_type": "evidence_pack",
            "trace_id": f"TRACE_{uuid.uuid4().hex[:8]}",
            "producer": "knowledge_base_agent",
            "version": 1,
            "payload": {
                "query": query or case.get('题目内容', '')[:100],
                "resolved_kp_ids": ["KP_ZD_001"],
                "evidence_items": [
                    {
                        "evidence_id": "E1",
                        "chunk_uid": f"CHUNK_{uuid.uuid4().hex[:8]}",
                        "source_title": case.get('题目章节来源', ''),
                        "content": case.get('题目答案', ''),
                        "kp_ids": [],
                        "scope": "public",
                        "authority_level": "textbook",
                        "confidence": 0.95,
                        "safety_level": "teaching_only"
                    }
                ],
                "conflict_evidence": [],
                "risk_notes": ["仅用于教学"]
            },
            "status": "success",
            "schema_version": "1.0.0"
        }


class TestAuditAgent(AuditAgent):
    """测试环境：简单审核，直接通过"""
    
    def review_content(self, draft: Dict, evidence_pack: Dict) -> Dict:
        return {
            "artifact_id": f"REVIEW_{uuid.uuid4().hex[:8]}",
            "artifact_type": "review_decision",
            "trace_id": f"TRACE_{uuid.uuid4().hex[:8]}",
            "producer": "audit_agent",
            "version": 1,
            "payload": {
                "draft_id": draft.get('artifact_id', ''),
                "decision": "pass",
                "scores": {
                    "factuality": 0.96,
                    "evidence_support": 0.94,
                    "difficulty_fit": 0.91,
                    "coverage": 0.90,
                    "format": 0.95,
                    "safety": 1.0
                },
                "weighted_total_score": 0.94,
                "overall_pass_threshold": 0.85,
                "hard_fail_matches": [],
                "verified_claims": [],
                "issues": [],
                "risk_flags": [],
                "revision_instruction": None,
                "human_review_reason": None
            },
            "status": "success",
            "schema_version": "1.0.0"
        }


class ProductionMemoryAgent(MemoryAgent):
    def get_learner_context(self, user_id: str) -> Dict:
        return {
            "artifact_id": f"LC_{user_id}",
            "artifact_type": "learner_context",
            "trace_id": f"TRACE_{uuid.uuid4().hex[:8]}",
            "producer": "memory_agent",
            "version": 1,
            "payload": {
                "user_id": user_id,
                "stable_profile": {"user_group": "", "daily_available_minutes": 30},
                "confirmed_preferences": {},
                "relevant_memories": [],
                "temporary_constraints": [],
                "memory_conflicts": [],
                "profile_update_suggestions": [],
            },
            "status": "success",
            "schema_version": "1.0.0",
        }


class ProductionDiagnosisAgent(DiagnosisAgent):
    def get_diagnosis_result(self, user_id: str) -> Dict:
        return {
            "artifact_id": f"DIA_{user_id}",
            "artifact_type": "diagnosis_result",
            "trace_id": f"TRACE_{uuid.uuid4().hex[:8]}",
            "producer": "diagnosis_agent",
            "version": 1,
            "payload": {
                "learner_id": user_id,
                "stage_id": "T0",
                "stage_name": "stable",
                "weak_kp_ids": [],
                "severity": "low",
                "confidence": 0.5,
                "primary_attribution": "",
                "evidence": [],
                "suggested_action": {},
            },
            "status": "success",
            "schema_version": "1.0.0",
        }


# ==================== Agent 工厂 ====================

class AgentFactory:
    """Agent 工厂 - 根据环境创建对应的 Agent 实例"""
    
    @staticmethod
    def create_memory_agent(test_data: Dict = None) -> MemoryAgent:
        """创建记忆管理 Agent"""
        if test_data is not None:
            return TestMemoryAgent(test_data)
        return ProductionMemoryAgent()
    
    @staticmethod
    def create_diagnosis_agent(test_data: Dict = None) -> DiagnosisAgent:
        """创建学情分析 Agent"""
        if test_data is not None:
            return TestDiagnosisAgent(test_data)
        return ProductionDiagnosisAgent()
    
    @staticmethod
    def create_expert_agent(llm_provider=None) -> ExpertAgent:
        """创建专家 Agent"""
        if llm_provider is not None:
            return TestExpertAgent(llm_provider)
        raise NotImplementedError("生产环境请实现真实的 ExpertAgent")
    
    @staticmethod
    def create_knowledge_base_agent() -> KnowledgeBaseAgent:
        """创建知识库管理 Agent"""
        return TestKnowledgeBaseAgent()
    
    @staticmethod
    def create_audit_agent() -> AuditAgent:
        """创建审核裁判 Agent"""
        return TestAuditAgent()