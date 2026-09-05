"""CMB 验证集适配层：清洗 + 请求模板 + 字母集合提取/比对 + 纯规则判定。

CMB 数据格式（docs/CMB-val-merge.json）：
  {
    "exam_type": "专业知识考试",
    "exam_class": "中医学与中药学",
    "exam_subject": "中医学",
    "question": "肝主疏泄，主要表现在",
    "answer": "BCDE",          # 字符串，多选为无序字母集合
    "option": {"A": "...", "B": "...", ...},   # 字典
    "question_type": "多项选择题",
    "explanation": "..."
  }

判定口径（最简版，纯规则通道A，不依赖 LLM Judge）：
  - 单选：提取到的字母集合 == {answer}
  - 多选：提取到的字母集合 == answer 集合（无序精确匹配，缺/多均算错）
  - 提取不到字母（拒答/讲解式回答）→ 记 answered=False，不计入准确率分母
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from judges import extract_answer_letter  # noqa: F401 —— 复用于兜底单字母提取


# ---------- 清洗与加载 ----------

def load_cmb(path: str | Path) -> list[dict[str, Any]]:
    """读取 CMB JSON，过滤脏数据，返回有效题目列表。

    过滤规则：dict 且 question/answer 非空、option 为 ≥2 项字典。
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    valid: list[dict[str, Any]] = []
    for d in raw:
        if not isinstance(d, dict):
            continue
        if not (d.get("question") and d.get("answer")):
            continue
        option = d.get("option")
        if not isinstance(option, dict) or len(option) < 2:
            continue
        valid.append(d)
    return valid


def build_user_request(q: dict[str, Any]) -> str:
    """题干 + 选项字典 → 与准确率测试一致的请求模板。"""
    lines = [str(q.get("question", "")).strip()]
    for key in sorted(q.get("option", {}), key=lambda k: (len(str(k)) != 1, str(k))):
        lines.append(f"{key}. {str(q['option'][key]).strip()}")
    lines.append("请给出答案选项字母，并简要说明依据。")
    return "\n".join(lines)


def expected_set(q: dict[str, Any]) -> set[str]:
    """answer 字符串 → 大写字母集合（多选无序）。"""
    return {ch.upper() for ch in str(q.get("answer", "")) if ch.isalpha()}


def question_kind(q: dict[str, Any]) -> str:
    """返回 'single' / 'multi'（按 answer 长度推断）。"""
    return "multi" if len(expected_set(q)) > 1 else "single"


def to_case(q: dict[str, Any], idx: int) -> dict[str, Any]:
    """题目 → 评测 case（供 EvaluationRunner 执行）。

    子集重跑数据可携带 ``_cmb_index``，以保留原始数据集中的题号；普通
    数据仍使用当前列表位置编号。
    """
    source_idx = int(q.get("_cmb_index", idx))
    subject = q.get("exam_subject") or q.get("exam_class") or "unknown"
    return {
        "case_id": f"cmb_{source_idx:04d}_{subject[:12]}",
        "user_request": build_user_request(q),
        "expected": str(q.get("answer", "")),  # 原样保留，判定用 expected_set
        "meta": {
            "cmb_index": source_idx,
            "exam_type": q.get("exam_type"),
            "exam_class": q.get("exam_class"),
            "exam_subject": q.get("exam_subject"),
            "question_type": q.get("question_type"),
            "kind": question_kind(q),
            "source": "cmb-val-merge",
        },
    }


def load_cmb_cases(path: str | Path) -> list[dict[str, Any]]:
    """清洗 + 编号 → cases 列表（全量 280 题直接跑）。"""
    return [to_case(q, i) for i, q in enumerate(load_cmb(path))]


# ---------- 字母集合提取 ----------

def extract_answer_letters(text: str) -> set[str]:
    """从系统输出提取选项字母集合。

    优先级：
      1. 「正确答案[:：]」「正确选项是」等显式标记（讲解类输出通常置于开头）
      2. 「答案[:：]」「答案为」「选/选择」等常见表达
      3. 行首选项列表（`A.` `B、` 多行，收集全部）
      4. 全文独立字母兜底

    提取前剥掉 markdown 加粗/引用符号（`**` `>`），避免干扰。
    """
    if not text:
        return set()
    text_upper = text.upper().replace("**", " ").replace(">", " ")
    patterns = [
        r"正确答案\s*[:：]?\s*([A-E][A-E\s,，、和至到~-]*)",
        r"正确选项\s*(?:是|为)?\s*[:：]?\s*([A-E][A-E\s,，、和至到~-]*)",
        r"答案\s*(?:为|是)?\s*[:：]\s*([A-E][A-E\s,，、和至到~-]*)",
        r"答案为\s*([A-E][A-E\s,，、和]*)",
        r"(?:选|选择)\s*([A-E](?:\s*[、,，和]\s*[A-E])*)",
    ]
    for pat in patterns:
        m = re.search(pat, text_upper, re.MULTILINE)
        if m:
            letters = {c for c in m.group(1) if c in "ABCDE"}
            if letters:
                return letters
    # 行首选项列表（多行）——收集所有匹配
    line_letters: set[str] = set()
    for m in re.finditer(r"^\(?([A-E][A-E]?)\)?[.、．\s]", text_upper, re.MULTILINE):
        line_letters |= {c for c in m.group(1) if c in "ABCDE"}
    if line_letters:
        return line_letters
    # 兜底：全文独立字母
    return set(re.findall(r"\b([A-E])\b", text_upper))


def answer_set_match(system_output: str, expected: str | set[str]) -> bool | None:
    """集合精确匹配。提取不到字母返回 None（未作答，不计入分母）。"""
    want = {str(ch).upper() for ch in expected} if isinstance(expected, set) else {
        ch.upper() for ch in str(expected) if ch.isalpha()
    }
    if not want:
        return None
    got = extract_answer_letters(system_output or "")
    if not got:
        return None
    return got == want


# ---------- 判定（纯规则通道A） ----------

def judge_cmb(record: dict[str, Any]) -> dict[str, Any]:
    """纯规则判定：字母集合比对，不调用 LLM。

    返回：
      answered    是否提取到答案字母（True/False）
      is_correct  答对与否（answered=True 时才有意义）
      got         提取到的字母（集合）
      want        期望字母（集合）
    """
    system_output = ""
    resource = record.get("resource") or {}
    content = resource.get("content") or {}
    if isinstance(content, dict):
        system_output = str(
            content.get("answer")
            or content.get("explanation")
            or content.get("正文")
            or json.dumps(content, ensure_ascii=False)[:800]
        )
    elif isinstance(content, str):
        system_output = content
    expected = record.get("input", {}).get("expected", "")
    got = extract_answer_letters(system_output)
    want = {ch.upper() for ch in str(expected) if ch.isalpha()}
    matched = answer_set_match(system_output, expected)
    return {
        "answered": bool(got),
        "is_correct": bool(matched) if matched is not None else False,
        "got": sorted(got),
        "want": sorted(want),
    }


# ---------- 判定（LLM Judge 通道B，CMB 专用） ----------

# Judge prompt 冻结版本——随报告提交，修改必须升版本
CMB_JUDGE_PROMPT_VERSION = "1.1"

CMB_JUDGE_SYSTEM = f"""你是中医客观题作答与幻觉判定裁判（prompt 版本 {CMB_JUDGE_PROMPT_VERSION}）。

任务分两部分，独立判定：
1. 作答正确性：判断系统回答是否给出了正确答案选项。
2. 幻觉判定：对系统回答的完整讲解内容按 C-FAITH 7 类做幻觉判定，与答案对错无关。

输入：
- 题目（含选项）
- 正确答案（如 "C" 或 "BCDE"）
- 系统回答（可能是完整讲解/解析的富文本）

作答正确性判定规则：
- 只要系统回答中给出的答案选项与正确答案一致 → is_correct=true
  （包括：直接给字母、或在解析中明确说明正确选项、或逐条辨析"✅ 正确/❌ 排除"能推出完整答案）
- 答案部分一致（多选漏选/多选多选）→ is_correct=false
- 系统回答未给出任何明确答案，或明确拒绝作答 → is_correct=false, answered=false
- 不因系统回答包含额外解释、追问、鼓励语而改变判定

幻觉判定规则（对讲解内容独立判定，答案选对也可能存在幻觉）：
- entity_error：实体错误（人物/机构/药物/部位等命名实体张冠李戴或不存在）
- relation_error：关系错误（因果/作用/归属等关系颠倒或虚构）
- attribute_error：属性错误（性质/机理/适应症等描述与事实不符）
- temporal_error：时空幻觉（时间/年代/阶段错置）
- fake_reference：虚假引用（编造不存在的教材/指南/文献引用，或引用编号与内容不符）
- fabricated_fact：虚构事实（凭空捏造无依据的事实陈述）
- stale_info：过时信息（已被更新指南/标准取代的旧信息）
- 答案选项字母错选本身不单独计为幻觉；只有讲解中出现的上述事实性错误才计 halu=true
- 表述不同但语义正确、正确但不全面、明确标注不确定 → 不算幻觉
- 拒答（answered=false）→ halu=false, halu_types=[]

严格输出 JSON：
{{"is_correct": bool, "answered": bool, "halu": bool, "halu_types": ["entity_error"], "reason": "一句话说明依据"}}
halu=true 时必须给出 halu_types；reason 需点明幻觉类别与具体内容（如"虚假引用：引用了不存在的《XX指南》第X章"）。"""


def build_judge_user(record: dict[str, Any]) -> str:
    """构造 judge 输入：题目 + 正确答案 + 系统回答。"""
    inp = record.get("input", {})
    expected = inp.get("expected", "")
    resource = record.get("resource") or {}
    content = resource.get("content") or {}
    if isinstance(content, dict):
        system_output = str(
            content.get("题目讲解")
            or content.get("answer")
            or content.get("explanation")
            or content.get("正文")
            or json.dumps(content, ensure_ascii=False)[:800]
        )
    elif isinstance(content, str):
        system_output = content
    else:
        system_output = ""
    return (
        f"题目：\n{inp.get('user_request', '')}\n\n"
        f"正确答案：{expected}\n\n"
        f"系统回答：\n{system_output or '(空)'}"
    )


async def judge_cmb_llm(judge, record: dict[str, Any]) -> dict[str, Any]:
    """LLM Judge 双通道（与规则通道A结合）：
    is_correct 以 LLM 判定为准；通道A 字母比对作参考，冲突标 conflict。
    LLM 调用失败时回退通道A，并标记 judge_error。
    """
    rule = judge_cmb(record)
    user = build_judge_user(record)
    try:
        results = await judge.judge(CMB_JUDGE_SYSTEM, user)
    except Exception as exc:  # noqa: BLE001
        return {
            **rule,
            "is_correct": rule["is_correct"],
            "answered": rule["answered"],
            "channel_a_correct": rule["is_correct"],
            "channel_a_answered": rule["answered"],
            "channel_b_correct": None,
            "channel_b_votes": 0,
            "halu": False,
            "halu_types": [],
            "halu_conflict": False,
            "conflict": False,
            "judge_error": str(exc)[:200],
            "reason": "LLM Judge 调用失败，回退规则判定：" + str(exc),
            "judge_rounds": [],
        }
    verdict, votes = judge.majority(results, "is_correct")
    answered_verdict, _ = judge.majority(results, "answered")
    # 幻觉多数决：全部轮次都判 halu=true 才计幻觉；部分轮次判算"待复核冲突"
    halu_flags = [bool(r.get("halu")) for r in results]
    halu_true_votes = sum(halu_flags)
    halu_verdict = len(results) > 0 and halu_true_votes == len(results)
    halu_conflict = 0 < halu_true_votes < len(results)
    # halu_types 与 reason 必须取自判 halu=true 的轮次，保证标记与理由自洽
    halu_rows = [r for r in results if r.get("halu")]
    if halu_verdict and halu_rows:
        halu_types = sorted({t for r in halu_rows for t in (r.get("halu_types") or [])})
        reason = halu_rows[0].get("reason") or ""
    else:
        halu_types = []
        reason = (results[0].get("reason") if results else "")
    llm_correct = verdict == "True"
    llm_answered = answered_verdict == "True" if answered_verdict is not None else rule["answered"]
    conflict = (
        rule["answered"]
        and llm_correct != rule["is_correct"]
    )
    return {
        **rule,
        "is_correct": llm_correct,
        "answered": llm_answered,
        "channel_a_correct": rule["is_correct"],
        "channel_a_answered": rule["answered"],
        "channel_b_correct": llm_correct,
        "channel_b_votes": votes,
        "halu": halu_verdict,
        "halu_types": halu_types,
        "halu_conflict": halu_conflict,
        "conflict": conflict,
        "reason": reason,
        # 保存 Judge 每轮原始判定，供日后改聚合口径离线重算，避免重复调 API
        "judge_rounds": results,
    }
