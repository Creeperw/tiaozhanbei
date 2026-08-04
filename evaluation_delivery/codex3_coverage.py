"""方案03：知识点覆盖率测试。

核心定义（与方案一致）：
- 核心知识点 = 黄金集中"有系统证据支撑"的知识点（分母）
- KPRecall@5/10：系统检索返回的证据中包含黄金 kp 的比例
- AnswerCoverage：covered_correct / covered_incorrect / not_covered
- 证据闭合率：检索证据中可追溯到知识库/题库的比例

前置核验：首次运行前用 --verify 对 10 个知识点做 kp_id 体系核验，
确认黄金集 kp_id 与系统检索 kp_id 同一体系，否则覆盖率分母按名称匹配。

用法：
  python codex3_coverage.py --verify        # kp_id 体系核验
  python codex3_coverage.py --limit 10      # 冒烟
  python codex3_coverage.py                 # 全量（100 kp × 2 题 = 200 题）
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
from judges import EmbeddingMatcher, LLMJudge, answer_letter_match  # noqa: E402
from stats import load_jsonl, render_proportion, write_report  # noqa: E402

# 黄金知识点集（示例：覆盖中药学/方剂学/诊断学/针灸学主干，评测方可按比赛大纲替换）
GOLD_KPS = [
    "四气五味", "升降浮沉", "归经", "中药配伍", "十八反", "十九畏",
    "麻黄汤", "桂枝汤", "四君子汤", "六味地黄丸", "补中益气汤", "逍遥散",
    "望诊", "闻诊", "问诊", "切诊", "舌诊", "脉诊", "八纲辨证", "脏腑辨证",
    "足三里", "合谷", "中脘", "关元", "涌泉", "肺俞", "经络循行", "奇经八脉",
    "五行学说", "阴阳学说", "气血津液", "津液代谢", "卫气营血", "六经辨证",
    "辨证论治", "治则治法", "汗法", "下法", "温法", "补法", "消法", "和法",
    "解表药", "清热药", "化痰止咳平喘药", "补虚药", "活血化瘀药", "理气药",
    "安神药", "平肝息风药", "开窍药", "收涩药", "涌吐药", "外用药",
    "感冒辨证", "咳嗽辨证", "哮喘辨证", "心悸辨证", "失眠辨证", "胃痛辨证",
    "头痛辨证", "眩晕辨证", "中风辨证", "水肿辨证", "泄泻辨证", "便秘辨证",
    "淋证", "消渴", "虚劳", "痹证", "痿证", "腰痛", "遗精", "带下病",
    "月经不调", "痛经", "小儿泄泻", "小儿咳嗽", "疳积", "乳痈", "痔疮",
    "湿疹", "蛇串疮", "面瘫", "中风后遗症", "颈椎病", "腰椎间盘突出",
    "针灸补泻手法", "灸法", "拔罐", "刮痧", "耳穴疗法", "穴位贴敷",
    "推拿手法", "小儿推拿", "脏腑推拿", "膏方", "药膳食疗", "情志养生",
    "子午流注", "五运六气", "体质辨识", "治未病", "亚健康调理", "中医护理",
]  # 100 个

VERIFY_SYSTEM = """你是知识体系对齐核验裁判。判定"系统检索证据"与"黄金知识点"是否指向同一知识点。

规则：
- 名称完全一致 → matched=true
- 名称不同但语义等价（如"麻黄汤的组成" vs "麻黄汤"）→ matched=true
- 同一体系但层级不同（如"方剂学" vs "麻黄汤"）→ matched=false（体系对但粒度不同）

严格输出 JSON：{"matched": bool, "same_system": bool, "reason": "..."}"""


def build_cases(kps: list[str], questions_by_kp: dict) -> list[dict]:
    """每个知识点取 2 题。题目从题库中按 kp 名检索；无匹配时用占位（评测方补数据）。"""
    cases = []
    for kp in kps:
        qs = questions_by_kp.get(kp, [])[:2]
        for i, q in enumerate(qs):
            options = q.get("options") or []
            text = q.get("question_content", "")
            for opt in options:
                text += f"\n{opt.get('option_id')}. {opt.get('content')}"
            cases.append(
                {
                    "case_id": f"cov_{kp[:8]}_{i}",
                    "user_request": f"题目：{text}\n请回答该题，并说明涉及哪些核心知识点。",
                    "expected": {
                        "kp": kp,
                        "gold_kp_ids": q.get("kp_ids", []),
                        "answer": q.get("answer", ""),  # 题库格式 ['C']
                    },
                    "meta": {"kp": kp, "question_id": q.get("question_id")},
                }
            )
    return cases


def load_questions_by_kp() -> dict[str, list[dict]]:
    """从题库按内容关键词建索引（真实题库 kp_ids 几乎为空，见质检结论）。

    匹配口径：题干 + 选项 + 解释全文包含黄金知识点名 → 命中。
    命中率受题库措辞影响，评测方可按比赛大纲补充黄金题数据。
    """
    papers_root = (
        Path(__file__).resolve().parents[1]
        / "backend/competition_app/data/qualification_papers/papers"
    )
    index: dict[str, list[dict]] = {}
    for f in sorted(papers_root.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        for q in data.get("questions", []):
            if q.get("question_type") != "single_choice":
                continue
            blob = " ".join(
                filter(
                    None,
                    [
                        str(q.get("question_content", "")),
                        " ".join(str(o.get("content", "")) for o in (q.get("options") or [])),
                        str(q.get("explanation", "")),
                    ],
                )
            )
            for kp in GOLD_KPS:
                if kp in blob:
                    index.setdefault(kp, []).append(q)
    return index


def extract_kp_names(record: dict) -> list[str]:
    """从系统输出提取知识点（EvidencePack → kp_ids/名称，兜底正文关键词）。"""
    names: list[str] = []
    content = (record.get("resource") or {}).get("content") or {}
    if isinstance(content, dict):
        for key in ("kp_ids", "knowledge_points", "kps"):
            names.extend(content.get(key) or [])
        for kp in GOLD_KPS:
            if kp in json.dumps(content, ensure_ascii=False):
                names.append(kp)
    elif isinstance(content, str):
        for kp in GOLD_KPS:
            if kp in content:
                names.append(kp)
    return names


async def verify_kp_system(judge: LLMJudge) -> dict:
    """10 题核验：黄金 kp 名 vs 系统证据 kp 名是否同一体系。"""
    index = load_questions_by_kp()
    results = []
    for kp in GOLD_KPS[:10]:
        qs = index.get(kp, [])
        if not qs:
            results.append({"kp": kp, "matched": None, "same_system": None, "reason": "题库无此知识点题目"})
            continue
        q = qs[0]
        sys_kp = ",".join(q.get("kp_ids", []) or [])
        user = f"黄金知识点：{kp}\n系统证据 kp_ids：{sys_kp}\n题目：{q.get('question_content', '')[:200]}"
        r = await judge.judge(VERIFY_SYSTEM, user)
        results.append({"kp": kp, **r[0]})
    matched = [r for r in results if r.get("matched") is True]
    return {
        "checked": len(results),
        "matched": len(matched),
        "results": results,
        "verdict": "同一体系 ✅" if len(matched) >= 8 else "体系不一致 ⚠️（覆盖率分母需按名称匹配）",
    }


async def run(args: argparse.Namespace) -> None:
    if args.verify:
        judge = LLMJudge()
        report = await verify_kp_system(judge)
        write_report(
            Path(__file__).parent / "outputs" / f"kp_verify_{args.run_tag}.md",
            "kp_id 体系核验报告",
            [("结论", report["verdict"]), ("明细", "\n".join(f"- {r['kp']}: matched={r.get('matched')} ({r.get('reason','')})" for r in report["results"]))],
        )
        return

    kps = GOLD_KPS[: args.limit] if args.limit else GOLD_KPS
    index = load_questions_by_kp()
    cases = build_cases(kps, index)
    if not cases:
        raise SystemExit("题库中无匹配题目——请在 GOLD_KPS 外补充黄金题数据（见 README）")

    config = RunConfig(
        output_dir=Path(__file__).parent / "outputs",
        workers=args.workers,
        learner_id=args.learner,
    )
    if args.env:
        config.env_file = Path(args.env)
    runner = EvaluationRunner(config)
    runner.build_container()
    print(f"[codex3] 开始执行 {len(cases)} 题…", file=sys.stderr)
    out_path = await runner.run_cases(cases, run_name=f"coverage_{args.run_tag}")

    # 判定：通道 A embedding 余弦 + 通道 B LLM
    matcher = EmbeddingMatcher()
    judge = LLMJudge()
    records = load_jsonl(out_path)
    judged = []
    for rec in records:
        if rec.get("skipped"):
            continue
        gold = (rec["input"].get("expected") or {}).get("kp", "")
        sys_kps = extract_kp_names(rec)
        sims = {}
        for skp in sys_kps:
            try:
                sims[skp] = await matcher.similarity(gold, skp)
            except Exception:
                sims[skp] = None
        best = max((v for v in sims.values() if v is not None), default=0.0)
        covered = best >= 0.75 if sims else False
        not_covered = best < 0.50 if sims else True
        user = f"黄金知识点：{gold}\n系统输出知识点：{','.join(sys_kps) or '(空)'}"
        r = await judge.judge(
            "判定系统回答是否覆盖了目标知识点。严格输出JSON：{\"covered\": bool, \"reason\": \"...\"}", user
        )
        verdict = r[0].get("covered")
        # 客观题正确性（AnswerCoverage 统计用）
        content = (rec.get("resource") or {}).get("content") or {}
        if isinstance(content, dict):
            system_output = str(
                content.get("answer")
                or content.get("explanation")
                or content.get("正文")
                or json.dumps(content, ensure_ascii=False)[:800]
            )
        elif isinstance(content, str):
            system_output = content
        is_correct = answer_letter_match(
            system_output, (rec["input"].get("expected") or {}).get("answer", "")
        )
        judged.append(
            {
                **rec,
                "gold_kp": gold,
                "sys_kps": sys_kps,
                "cosine_best": round(best, 4),
                "covered_embed": covered,
                "not_covered_embed": not_covered,
                "covered_llm": verdict,
                "covered": covered or verdict,
                "is_correct": is_correct,
                "conflict": covered != verdict,
            }
        )

    total = len(judged)
    valid = [r for r in judged if r.get("status") == "success"]
    covered = [r for r in valid if r.get("covered")]
    covered_correct = [r for r in covered if r.get("is_correct")]
    covered_incorrect = [r for r in covered if not r.get("is_correct")]
    not_covered = [r for r in valid if not r.get("covered")]
    conflicts = [r for r in judged if r.get("conflict")]
    closed = [r for r in valid if r.get("sys_kps")]

    sections = [
        ("执行概况", f"- 总用例: {total}\n- 成功: {len(valid)}"),
        ("KPRecall@5/10", render_proportion("覆盖", len(covered), len(valid) or 1, 0.9)),
        ("AnswerCoverage", f"- covered_correct: {len(covered_correct)}\n- covered_incorrect: {len(covered_incorrect)}\n- not_covered: {len(not_covered)}"),
        ("证据闭合率", render_proportion("有证据", len(closed), len(valid) or 1, 0.95)),
        ("双通道冲突", f"- {len(conflicts)} 组（待人工复核）"),
    ]
    report = Path(__file__).parent / "outputs" / f"coverage_report_{args.run_tag}.md"
    write_report(report, "知识点覆盖率测试报告", sections)


def main() -> None:
    p = argparse.ArgumentParser(description="覆盖率测试")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--workers", type=int, default=3)
    p.add_argument("--learner", default="eval-cov-user")
    p.add_argument("--env", default=None)
    p.add_argument("--run-tag", default=None)
    p.add_argument("--verify", action="store_true", help="kp_id 体系核验（10 题）")
    args = p.parse_args()
    args.run_tag = args.run_tag or time.strftime("%Y%m%d_%H%M%S")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
