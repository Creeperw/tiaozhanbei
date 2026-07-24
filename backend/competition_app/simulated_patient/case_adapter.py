"""
案例适配器 - 将 CMB 格式案例转换为组件内部格式
"""

import re
import random
from typing import Dict, Optional, List


class CaseAdapter:
    """CMB 案例适配器"""
    
    @classmethod
    def is_clinical_case(cls, raw_case: Dict) -> bool:
        """判断是否为临床案例"""
        return raw_case.get("题型", "") == "临床案例问答"
    
    @classmethod
    def adapt(cls, raw_case: Dict) -> Optional[Dict]:
        """适配案例"""
        if not cls.is_clinical_case(raw_case):
            return None

        case_id = raw_case.get("题目id", "")
        title = raw_case.get("题目章节来源", "未知科室")
        content = raw_case.get("题目内容", "")
        answer = raw_case.get("题目答案", "")
        tags = raw_case.get("标签", [])

        parsed_content = cls._parse_content(content, title, answer)
        parsed_answer = cls._parse_answer(answer)

        return {
            "id": case_id,
            "name": parsed_answer.get("diagnosis", cls._generate_diagnosis_name(title, parsed_content)),
            "department": cls._infer_department(title, tags, parsed_answer.get("diagnosis", "")),
            "syndrome": parsed_answer.get("diagnosis", cls._generate_diagnosis_name(title, parsed_content)),
            "prescription_name": "",
            "prescription_composition": "",
            "special_notes": cls._extract_special_notes(content),
            "symptoms": parsed_content.get("symptoms", cls._generate_symptoms(title, parsed_answer.get("diagnosis", ""))),
            "signs": parsed_content.get("signs", cls._generate_signs(title, parsed_answer.get("diagnosis", ""))),
            "age_range": parsed_content.get("age_range", cls._generate_age_range(content, title)),
            "gender_preference": parsed_content.get("gender", cls._generate_gender(content, title)),
            "medical_history": parsed_content.get("medical_history", "无特殊病史"),
            "family_history": parsed_content.get("family_history", "无特殊家族史"),
            "key_points": parsed_answer.get("key_points", []),
            "knowledge": {
                "kp001": {
                    "name": parsed_answer.get("diagnosis", title),
                    "description": parsed_answer.get("diagnosis_basis", "")
                }
            },
            "source_type": "clinical_case",
            "raw_data": raw_case
        }

    @classmethod
    def _parse_content(cls, content: str, title: str, answer: str) -> Dict:
        """从题目内容提取患者信息"""
        result = {
            "symptoms": "",
            "signs": "",
            "age_range": "中年（35-55岁）",
            "gender": "男",
            "medical_history": "无特殊病史",
            "family_history": "无特殊家族史"
        }

        # 提取年龄和性别
        age_gender = re.search(r'([男女])[，,、]\s*(\d{1,3})岁', content)
        if age_gender:
            result["gender"] = age_gender.group(1)
            age = int(age_gender.group(2))
            result["age_range"] = cls._age_to_range(age)
        else:
            age_match = re.search(r'(\d{1,3})岁', content)
            if age_match:
                age = int(age_match.group(1))
                result["age_range"] = cls._age_to_range(age)
            gender_match = re.search(r'([男女])性', content)
            if gender_match:
                result["gender"] = gender_match.group(1)

        if "妇科" in title:
            result["gender"] = "女"

        # 提取主诉/症状
        chief_match = re.search(r'主诉[：:]\s*(.+?)(?=\n|$)', content)
        if chief_match:
            result["symptoms"] = chief_match.group(1).strip()

        # 提取体格检查
        signs_parts = []
        pe_match = re.search(r'体格检查[：:]\s*(.+?)(?=辅助检查|\n\n|$)', content, re.DOTALL)
        if pe_match:
            signs_parts.append(cls._clean_text(pe_match.group(1).strip()))

        # 提取辅助检查
        aux_match = re.search(r'辅助检查[：:]\s*(.+?)(?=问题|$)', content, re.DOTALL)
        if aux_match:
            signs_parts.append("辅助检查：" + cls._clean_text(aux_match.group(1).strip()))

        # 提取生命体征
        vital_signs = re.findall(r'(T\s*[\d.]+\s*°C|BP\s*[\d/]+|P\s*\d+|\d+/\d+\s*mmHg)', content)
        for vs in vital_signs:
            if vs and vs not in str(signs_parts):
                signs_parts.append(vs)

        result["signs"] = "；".join(signs_parts) if signs_parts else "未见明显异常体征"

        if len(result["symptoms"]) > 300:
            result["symptoms"] = result["symptoms"][:300] + "..."

        return result

    @classmethod
    def _parse_answer(cls, answer: str) -> Dict:
        """从题目答案提取诊断信息"""
        result = {"diagnosis": "", "key_points": [], "diagnosis_basis": ""}

        diag_match = re.search(r'诊断[：:]\s*(.+?)(?=\n|$)', answer)
        if diag_match:
            result["diagnosis"] = diag_match.group(1).strip()
        else:
            first = answer.split("。")[0] if "。" in answer else answer[:50]
            first = re.sub(r'诊断依据[：:].*$', '', first)
            result["diagnosis"] = first.strip()

        basis_match = re.search(r'诊断依据[：:]\s*(.+)', answer, re.DOTALL)
        if basis_match:
            basis_text = basis_match.group(1).strip()
            result["diagnosis_basis"] = basis_text
            items = re.split(r'[①②③④⑤⑥⑦⑧⑨⑩]', basis_text)
            result["key_points"] = [item.strip() for item in items if item.strip()]

        if not result["key_points"] and result["diagnosis_basis"]:
            if "；" in result["diagnosis_basis"]:
                result["key_points"] = [p.strip() for p in result["diagnosis_basis"].split("；") if p.strip()]

        if not result["key_points"]:
            result["key_points"] = [result["diagnosis_basis"]]

        return result

    @classmethod
    def _infer_department(cls, title: str, tags: List[str], diagnosis: str) -> str:
        """推断科室"""
        dept_map = {
            "腹外疝": "外科", "疝": "外科", "阑尾": "外科", "胆": "外科",
            "骨折": "骨伤科", "关节": "骨伤科", "脊柱": "骨伤科",
            "消化": "内科", "呼吸": "内科", "心血管": "内科",
            "高血压": "内科", "糖尿病": "内科", "肾病": "内科",
            "妇": "妇科", "子宫": "妇科", "卵巢": "妇科", "月经": "妇科",
            "儿": "儿科", "小儿": "儿科",
            "皮肤": "皮肤科", "疹": "皮肤科", "癣": "皮肤科",
            "神经": "内科", "脑": "内科",
            "内分泌": "内科", "甲状腺": "内科"
        }
        combined = title + " " + " ".join(tags) + " " + diagnosis
        for key, dept in dept_map.items():
            if key in combined:
                return dept
        return "内科"

    @classmethod
    def _age_to_range(cls, age: int) -> str:
        """年龄转范围"""
        if age <= 12:
            return "儿童（5-12岁）"
        elif age <= 35:
            return "青年（20-35岁）"
        elif age <= 55:
            return "中年（35-55岁）"
        else:
            return "老年（55岁以上）"

    @classmethod
    def _clean_text(cls, text: str) -> str:
        """清理文本"""
        if not text:
            return ""
        text = text.replace("\n", " ")
        text = re.sub(r'\s+', " ", text)
        return text.strip()

    @classmethod
    def _extract_special_notes(cls, content: str) -> str:
        """提取注意事项"""
        notes = []
        if "忌" in content:
            match = re.search(r'忌[：:]\s*(.+?)(?=\n|$)', content)
            if match:
                notes.append(match.group(1).strip())
        return "；".join(notes) if notes else "无特殊注意事项"

    @classmethod
    def _generate_diagnosis_name(cls, title: str, parsed_content: Dict) -> str:
        """生成诊断名称"""
        if "腹外疝" in title:
            return "腹外疝"
        if "消化" in title:
            return "消化系统疾病"
        if "呼吸" in title:
            return "呼吸系统疾病"
        if "心血管" in title:
            return "心血管疾病"
        if "骨科" in title:
            return "骨科疾病"
        if "妇" in title:
            return "妇科疾病"
        if "儿" in title:
            return "儿科疾病"
        return title.replace("案例分析-", "").replace("案例分析", "临床案例")

    @classmethod
    def _generate_symptoms(cls, title: str, diagnosis: str) -> str:
        """生成症状"""
        symptom_map = {
            "腹外疝": "腹部包块，站立或用力时明显，平卧后可回纳，局部坠胀感",
            "消化": "腹痛、腹胀、恶心、食欲减退",
            "呼吸": "咳嗽、咳痰、气喘、胸闷",
            "心血管": "心悸、胸闷、气短、乏力",
            "骨科": "疼痛、活动受限、肿胀",
            "妇科": "月经异常、腹痛、带下异常"
        }
        for key, symptom in symptom_map.items():
            if key in title:
                return symptom
        if diagnosis:
            return f"与{diagnosis}相关的症状表现"
        return "无明显特异性症状，需进一步问诊"

    @classmethod
    def _generate_signs(cls, title: str, diagnosis: str) -> str:
        """生成体征"""
        sign_map = {
            "腹外疝": "腹股沟区可扪及包块，有压痛，咳嗽冲击感阳性",
            "消化": "腹部压痛，肠鸣音异常",
            "呼吸": "呼吸音粗，可闻及干湿啰音",
            "心血管": "心率异常，血压异常",
            "骨科": "局部压痛，活动受限，关节肿胀",
            "妇科": "腹部压痛，妇科检查异常"
        }
        for key, sign in sign_map.items():
            if key in title:
                return sign
        return "需进行体格检查进一步明确"

    @classmethod
    def _generate_age_range(cls, content: str, title: str) -> str:
        """生成年龄范围"""
        age_match = re.search(r'(\d{1,3})岁', content)
        if age_match:
            age = int(age_match.group(1))
            return cls._age_to_range(age)

        if "儿科" in title or "小儿" in title:
            return "儿童（5-12岁）"
        if "妇科" in title:
            return random.choice(["青年（20-35岁）", "中年（35-55岁）"])
        if "老年" in title or "退行性" in title:
            return "老年（55岁以上）"
        return random.choice(["青年（20-35岁）", "中年（35-55岁）", "老年（55岁以上）"])

    @classmethod
    def _generate_gender(cls, content: str, title: str) -> str:
        """生成性别"""
        if "女" in content or "她" in content:
            return "女"
        if "男" in content or "他" in content:
            return "男"

        if "妇科" in title:
            return "女"
        if "前列腺" in title or "阳痿" in title:
            return "男"
        return random.choice(["男", "女"])