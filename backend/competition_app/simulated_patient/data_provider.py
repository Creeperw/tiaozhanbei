"""
数据提供者 - 负责数据持久化（错题、收藏、统计、历史、对话）
"""

import os
import json
import random
from typing import List, Dict, Optional
from datetime import datetime


class DefaultDataProvider:
    """数据持久化提供者"""
    
    def __init__(self, case_data_path: str = None, data_dir: str = "./simulated_patient_data"):
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)

        # 加载临床案例（CMB格式）
        self.cases = []
        if case_data_path and os.path.exists(case_data_path):
            with open(case_data_path, 'r', encoding='utf-8') as f:
                self.cases = json.load(f)

        # 错题/收藏/统计/历史持久化
        self._mistakes_db = self._load("mistakes.json", {})
        self._collections_db = self._load("collections.json", {})
        self._stats_db = self._load("stats.json", {})
        self._history_db = self._load("history.json", {})

    # ========== 案例管理 ==========

    def get_case_by_id(self, case_id: str) -> Optional[Dict]:
        """根据ID获取案例"""
        for c in self.cases:
            if c.get('题目id') == case_id:
                return c
        return None

    def get_cases_for_user(self, weak_kp_ids: List[str] = None) -> List[Dict]:
        """根据薄弱知识点筛选案例"""
        if not weak_kp_ids:
            return self.cases

        matched = []
        for case in self.cases:
            tags = case.get('标签', [])
            source = case.get('题目章节来源', '')
            combined = ' '.join(tags) + ' ' + source
            for kp in weak_kp_ids:
                if kp in combined or any(kp in tag for tag in tags):
                    matched.append(case)
                    break
        return matched if matched else self.cases

    def get_random_case(self, weak_kp_ids: List[str] = None) -> Dict:
        """随机获取一个案例"""
        cases = self.get_cases_for_user(weak_kp_ids)
        return random.choice(cases) if cases else {}

    def search_cases(self, keyword: str, department: Optional[str] = None) -> List[Dict]:
        """搜索案例"""
        result = []
        for c in self.cases:
            if keyword in c.get('题目章节来源', '') or keyword in c.get('题目内容', ''):
                if department and department not in c.get('标签', []):
                    continue
                result.append(c)
        return result

    # ========== 错题库 ==========

    def get_mistakes(self, user_id: str, limit: int = 100, offset: int = 0) -> List[Dict]:
        """获取错题列表"""
        records = self._mistakes_db.get(user_id, [])
        records = sorted(records, key=lambda x: x.get('timestamp', ''), reverse=True)
        return records[offset:offset+limit]

    def add_mistake_record(self, user_id: str, case_id: str, user_answers: Dict, report: Dict, history_id: str = "", session_id: str = "") -> bool:
        """添加错题"""
        data = self._mistakes_db
        if user_id not in data:
            data[user_id] = []
        case = self.get_case_by_id(case_id)
        data[user_id].append({
            "mistake_id": f"M_{len(data[user_id])+1:03d}",
            "case_id": case_id,
            "case_name": case.get('题目章节来源', '') if case else '',
            "user_answers": user_answers,
            "score": report.get('score', 0),
            "diagnosis_correct": report.get('diagnosis_correct', False),
            "timestamp": datetime.now().isoformat(),
            "review_flag": True,
            "history_id": history_id,
            "session_id": session_id,
            "report": report,
        })
        self._save("mistakes.json", data)
        return True

    def update_mistake_review(self, mistake_id: str, user_comment: str) -> bool:
        """更新错题复习状态"""
        data = self._mistakes_db
        for user_id, records in data.items():
            for r in records:
                if r.get('mistake_id') == mistake_id:
                    r['user_comment'] = user_comment
                    r['review_flag'] = False
                    self._save("mistakes.json", data)
                    return True
        return False

    # ========== 收藏夹 ==========

    def get_collections(self, user_id: str) -> List[Dict]:
        """获取收藏列表"""
        return self._collections_db.get(user_id, [])

    def get_collection_count(self, user_id: str) -> int:
        """获取收藏数量"""
        return len(self.get_collections(user_id))

    def add_collection(self, user_id: str, case_id: str) -> bool:
        """添加收藏"""
        data = self._collections_db
        if user_id not in data:
            data[user_id] = []
        for c in data[user_id]:
            if c.get('case_id') == case_id:
                return False
        case = self.get_case_by_id(case_id)
        data[user_id].append({
            "case_id": case_id,
            "case_name": case.get('题目章节来源', '') if case else '',
            "collected_at": datetime.now().isoformat()
        })
        self._save("collections.json", data)
        return True

    def remove_collection(self, user_id: str, case_id: str) -> bool:
        """移除收藏"""
        data = self._collections_db
        if user_id in data:
            data[user_id] = [c for c in data[user_id] if c.get('case_id') != case_id]
            self._save("collections.json", data)
            return True
        return False

    # ========== 统计 ==========

    def record_stats(self, user_id: str, case_id: str, correct: bool, score: int, mode: str) -> bool:
        """记录学习统计"""
        data = self._stats_db
        if user_id not in data:
            data[user_id] = {"history": []}
        data[user_id]["history"].append({
            "date": datetime.now().strftime("%Y-%m-%d"),
            "case_id": case_id,
            "correct": correct,
            "score": score,
            "mode": mode,
            "timestamp": datetime.now().isoformat()
        })
        self._save("stats.json", data)
        return True

    def get_daily_stats(self, user_id: str) -> Dict:
        """获取今日统计"""
        data = self._stats_db.get(user_id, {})
        today = datetime.now().strftime("%Y-%m-%d")
        records = [r for r in data.get("history", []) if r.get('date') == today]
        total = len(records)
        if total == 0:
            return {"total": 0, "correct": 0, "wrong": 0, "rate": 0, "streak": 0, "pending_review": 0}
        correct = sum(1 for r in records if r.get('correct', False))
        wrong = total - correct
        rate = round(correct / total * 100) if total > 0 else 0
        pending = len([r for r in self.get_mistakes(user_id) if r.get('review_flag', True)])
        return {"total": total, "correct": correct, "wrong": wrong, "rate": rate, "streak": 0, "pending_review": pending}

    # ========== 历史记录 ==========

    def save_history_record(self, user_id: str, record: Dict) -> bool:
        """保存提交历史"""
        data = self._history_db
        if user_id not in data:
            data[user_id] = []
        data[user_id].append(record)
        self._save("history.json", data)
        return True

    def get_history_detail(self, history_id: str) -> Dict:
        """获取历史详情"""
        for user_id, records in self._history_db.items():
            for r in records:
                if r.get('history_id') == history_id:
                    return r
        return {}

    def get_all_history(self, user_id: str) -> List[Dict]:
        """获取所有历史记录"""
        records = self._history_db.get(user_id, [])
        return sorted(records, key=lambda x: x.get('timestamp', ''), reverse=True)

    # ========== 对话历史持久化 ==========

    def save_dialog_entry(self, user_id: str, entry: Dict) -> bool:
        """保存对话记录"""
        dialog_file = os.path.join(self.data_dir, f"dialog_{user_id}.json")
        if os.path.exists(dialog_file):
            with open(dialog_file, 'r', encoding='utf-8') as f:
                history = json.load(f)
        else:
            history = []
        history.append(entry)
        with open(dialog_file, 'w', encoding='utf-8') as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
        return True

    def get_dialog_history(self, user_id: str, limit: int = 100) -> List[Dict]:
        """获取对话历史"""
        dialog_file = os.path.join(self.data_dir, f"dialog_{user_id}.json")
        if os.path.exists(dialog_file):
            with open(dialog_file, 'r', encoding='utf-8') as f:
                history = json.load(f)
                return history[-limit:] if limit > 0 else history
        return []

    # ========== 文件 IO ==========

    def _load(self, filename: str, default: Dict) -> Dict:
        """加载 JSON 文件"""
        path = os.path.join(self.data_dir, filename)
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        return default

    def _save(self, filename: str, data: Dict):
        """保存 JSON 文件"""
        path = os.path.join(self.data_dir, filename)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)