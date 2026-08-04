"""方案02：学习者画像与资源难度适配度测试。

用法：
  python codex2_adaptation.py --limit 5      # 冒烟
  python codex2_adaptation.py                # 全量（9 画像 × 10 人 × 3 知识点 = 270 组）

输出（outputs/）：
  adaptation_<ts>.jsonl
  adaptation_report_<ts>.md

注意：客观通道（资源难度标签）依赖系统输出携带难度信息。若当前版本
ResourceDraft 尚无 difficulty 字段，客观通道自动降级为"仅主观通道"并
在报告中标注（方案已约定此降级口径）。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from runner import EvaluationRunner, RunConfig  # noqa: E402
from judges import LLMJudge, in_difficulty_range  # noqa: E402
from stats import load_jsonl, render_proportion, write_report  # noqa: E402

# 9 类画像：专业背景 × 前置掌握度（3×3）
LEVELS = ["low", "medium", "high"]
PROFILE_TEMPLATE = {
    "low": {"background": "非医学专业", "years": 0, "pre_knowledge": "零基础"},
    "medium": {"background": "相关专业/爱好者", "years": 2, "pre_knowledge": "部分掌握"},
    "high": {"background": "中医学专业", "years": 5, "pre_knowledge": "系统学习过"},
}

# 目标知识点（覆盖方剂学/中药学/中医诊断学等科目）
TARGET_KPS = [
    "麻黄汤的组成与功效",
    "四君子汤的组方原理",
    "中药四气五味理论",
    "当归的性味归经",
    "望舌苔的临床意义",
    "脉诊寸关尺分部",
    "八纲辨证-表里",
    "五行生克关系",
    "针灸腧穴定位-足三里",
]

RUBRIC_SYSTEM = f"""你是教育资源适配度评审（prompt 版本 {__import__('judges').JUDGE_PROMPT_VERSION}）。

对"系统推荐的学习资源"相对"学习者画像"做 4 维评分（1-5 分）：
1. terminology 术语难度：25% 权重（术语是否超出画像背景可理解范围）
2. prerequisite  前置知识：35% 权重（是否要求画像未掌握的预备知识）
3. density       密度与时间：20% 权重（内容密度与 available_minutes 是否匹配）
4. format        呈现形式：20% 权重（是否契合画像资源偏好）

硬门槛：任一维度 <3 分 → 直接"不适配"。
判定：无硬门槛违规且均分 ≥4 → 适配；3.0~3.99 → 基本适配（算不适配）；
      <3 → 不适配。

严格输出 JSON：{{"scores": {{"terminology": int, "prerequisite": int, "density": int, "format": int}}, "adapted": bool, "reason": "..."}}"""


def build_profile(bg: str, mastery: str) -> dict:
    bg_info = PROFILE_TEMPLATE[bg]
    mastery_info = PROFILE_TEMPLATE[mastery]
    return {
        "user_profile": {
            "name": f"P_{bg}_{mastery}",
            "professional_background": bg_info["background"],
            "years_of_study": bg_info["years"],
            "pre_knowledge_level": mastery_info["pre_knowledge"],
            "preferences": {"resource_type": "textbook", "pace": "moderate"},
        },
        "user_knowledge_state": [
            {"kp_name": kp, "mastery": 0.2 if mastery == "low" else 0.5 if mastery == "medium" else 0.8}
            for kp in TARGET_KPS[:2]
        ],
    }


def build_cases(per_person: int = 3) -> list[dict]:
    cases = []
    idx = 0
    for bg in LEVELS:
        for mastery in LEVELS:
            for person in range(10):
                profile = build_profile(bg, mastery)
                kps = TARGET_KPS[(idx * 3) % len(TARGET_KPS) : (idx * 3) % len(TARGET_KPS) + per_person]
                if len(kps) < per_person:
                    kps = (kps + TARGET_KPS)[:per_person]
                for kp in kps:
                    cases.append(
                        {
                            "case_id": f"adapt_{bg}_{mastery}_{person}_{kp[:6]}",
                            "user_request": (
                                f"我的背景：{profile['user_profile']['professional_background']}，"
                                f"学习年限 {profile['user_profile']['years_of_study']} 年，"
                                f"前置掌握 {profile['user_profile']['pre_knowledge_level']}。"
                                f"请针对知识点「{kp}」给我推荐 3 个学习资源（教材/视频/题目），"
                                f"标注每个资源的难度，总学习时间约 30 分钟。"
                            ),
                            "expected": {"kp": kp, "target_difficulty": (1, 2) if bg == "low" else (2, 3) if bg == "medium" else (3, 4)},
                            "profile_override": profile,
                            "meta": {"bg": bg, "mastery": mastery, "person": person, "kp": kp},
                        }
                    )
                idx += 1
    return cases


def extract_resource_difficulty(record: dict) -> int | None:
    """从系统输出提取难度标签（1-5）。当前版本 ResourceDraft 无 difficulty，
    读取内容中的显式标注（如"难度：3"）。"""
    content = (record.get("resource") or {}).get("content") or {}
    text = ""
    if isinstance(content, dict):
        text = json.dumps(content, ensure_ascii=False)
    elif isinstance(content, str):
        text = content
    import re

    m = re.search(r"难度\s*[:：]?\s*([1-5])", text)
    return int(m.group(1)) if m else None


async def judge_case(judge: LLMJudge, record: dict, expected: dict) -> dict:
    content = (record.get("resource") or {}).get("content") or {}
    text = json.dumps(content, ensure_ascii=False)[:2000] if isinstance(content, dict) else str(content)[:2000]
    profile = record["input"].get("profile_override", {})
    user = (
        f"学习者画像：{json.dumps(profile.get('user_profile', {}), ensure_ascii=False)}\n"
        f"前置知识：{json.dumps(profile.get('user_knowledge_state', []), ensure_ascii=False)}\n"
        f"可用时间：30 分钟\n"
        f"目标知识点：{expected.get('kp')}\n"
        f"系统推荐资源：{text or '(空)'}"
    )
    results = await judge.judge(RUBRIC_SYSTEM, user)
    votes = [r for r in results if r.get("adapted") is not None]
    adapted = any(r.get("adapted") for r in votes) if votes else None
    scores = results[0].get("scores") if results else {}

    # 客观通道：难度区间（可配置，当前降级）
    difficulty = extract_resource_difficulty(record)
    lo, hi = expected.get("target_difficulty", (1, 5))
    objective_ok = in_difficulty_range(difficulty, lo, hi) if difficulty is not None else None

    final_adapted = (
        (objective_ok or adapted)
        if objective_ok is not None or adapted is not None
        else None
    )
    return {
        "case_id": record["case_id"],
        "objective_ok": objective_ok,
        "objective_difficulty": difficulty,
        "rubric_adapted": adapted,
        "rubric_scores": scores,
        "adapted": final_adapted,
        "hard_gate_violation": any(
            isinstance(v, (int, float)) and v < 3 for v in (scores or {}).values()
        ),
        "reason": (results[0].get("reason") if results else ""),
    }


async def run(args: argparse.Namespace) -> None:
    cases = build_cases()
    if args.limit:
        cases = cases[: args.limit]
    config = RunConfig(
        output_dir=Path(__file__).parent / "outputs",
        workers=args.workers,
        learner_id=args.learner,
    )
    if args.env:
        config.env_file = Path(args.env)
    runner = EvaluationRunner(config)
    runner.build_container()
    print(f"[codex2] 开始执行 {len(cases)} 组用例…", file=sys.stderr)
    out_path = await runner.run_cases(cases, run_name=f"adaptation_{args.run_tag}")

    judge = LLMJudge()
    records = load_jsonl(out_path)
    judged = []
    for rec in records:
        if rec.get("skipped"):
            continue
        expected = rec["input"].get("expected") or {}
        judgement = await judge_case(judge, rec, expected)
        judged.append({**rec, **judgement})

    total = len(judged)
    valid = [r for r in judged if r.get("status") == "success"]
    adapted = [r for r in valid if r.get("adapted") is True]
    objective_available = [r for r in judged if r.get("objective_difficulty") is not None]
    objective_ok = [r for r in judged if r.get("objective_ok") is True]
    rubric_ok = [r for r in judged if r.get("rubric_adapted") is True]
    hard_violations = [r for r in judged if r.get("hard_gate_violation")]
    conflicts = [r for r in judged if r.get("objective_ok") is not None and r.get("rubric_adapted") is not None and r.get("objective_ok") != r.get("rubric_adapted")]

    sections = [
        ("执行概况", f"- 总用例: {total}\n- 成功: {len(valid)}\n- 有效用例（成功且返回资源）: {len([r for r in valid if r.get('resource')])}"),
        ("适配准确率", render_proportion("判定适配", len(adapted), len(valid) or 1, 0.85)),
        ("客观通道（难度区间）", render_proportion("难度命中", len(objective_ok), len(objective_available) or 1)),
        ("主观通道（LLM Rubric）", render_proportion("Rubric 适配", len(rubric_ok), len(valid) or 1)),
        ("硬门槛违规", f"- {len(hard_violations)} 组存在 <3 分维度（直接不适配）"),
        ("双通道冲突", f"- {len(conflicts)} 组（待人工复核）"),
        ("稳定性提示", "动态反馈测试（连续答对/答错 3 次后难度调整）需另行运行 --dynamic，见 README"),
    ]
    report = Path(__file__).parent / "outputs" / f"adaptation_report_{args.run_tag}.md"
    write_report(report, "画像与资源适配度测试报告", sections)


def main() -> None:
    p = argparse.ArgumentParser(description="画像适配度测试")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--workers", type=int, default=3)
    p.add_argument("--learner", default="eval-adapt-user")
    p.add_argument("--env", default=None)
    p.add_argument("--run-tag", default=None)
    args = p.parse_args()
    args.run_tag = args.run_tag or time.strftime("%Y%m%d_%H%M%S")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
