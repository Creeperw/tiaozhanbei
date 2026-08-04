"""方案01：专业知识谬误率（幻觉率）测试。

用法：
  export EVAL_JUDGE_API_KEY=sk-xxx            # LLM Judge（DeepSeek）
  python codex1_hallucination.py --limit 20    # 先小批量冒烟
  python codex1_hallucination.py               # 全量（默认抽样 1000 题）

输出（outputs/）：
  hallucination_<ts>.jsonl  每 case 一行原始结果
  hallucination_report.md   汇总报告（模型级/系统级幻觉率 + 7 类分布 + CI）
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from runner import EvaluationRunner, RunConfig  # noqa: E402
from judges import LLMJudge, answer_letter_match, extract_answer_letter  # noqa: E402
from stats import load_jsonl, render_proportion, wilson_ci, write_report, count_by  # noqa: E402

# C-FAITH 7 类幻觉定义（冻结）
HALU_TYPES = [
    "entity_error",     # 实体错误（人名/方名/穴位名张冠李戴）
    "relation_error",   # 关系错误（药物与归经/功效关系颠倒）
    "attribute_error",  # 属性错误（性味归经/剂量/配伍禁忌错误）
    "temporal_error",   # 时空幻觉（朝代/医家年代错位）
    "fake_reference",   # 虚假引用（引用不存在的典籍/条文）
    "fabricated_fact",  # 虚构事实（凭空编造知识点）
    "stale_info",       # 过时信息（已修订的旧知识）
]

JUDGE_SYSTEM = f"""你是中医药领域幻觉判定裁判（prompt 版本 {__import__('judges').JUDGE_PROMPT_VERSION}）。

任务：判定系统对一道中医客观题的回答是否准确，并按 C-FAITH 7 类幻觉分类。

判定规则：
- 正确答案（expected）与系统回答对比：
  - 选项字母一致 → is_correct=true
  - 字母不一致，或未给字母 → 继续读“依据”部分是否含幻觉
- 幻觉定义（任一项即 halu=true）：
  1. entity_error：实体错误（人物/方剂/穴位/著作名错误）
  2. relation_error：关系错误（药物-功效-归经关系颠倒）
  3. attribute_error：属性错误（性味/剂量/配伍禁忌错误）
  4. temporal_error：时空幻觉（朝代/年代错位）
  5. fake_reference：虚假引用（不存在的典籍或条文）
  6. fabricated_fact：虚构事实
  7. stale_info：过时信息
- 不算幻觉：表述不同但语义相同、正确但不全面、明确标注不确定、拒答。

严格输出 JSON：
{{"is_correct": bool, "halu": bool, "halu_types": ["entity_error"], "reason": "一句话说明"}}"""


def build_user_request(question: dict) -> str:
    """题干+选项 → 与准确率测试一致的请求模板。"""
    options = question.get("options") or []
    lines = [question.get("question_content", "")]
    for opt in options:
        lines.append(f"{opt.get('option_id')}. {opt.get('content')}")
    lines.append("请给出答案选项字母，并简要说明依据。")
    return "\n".join(lines)


def load_questions(sample_size: int, seed: int) -> list[tuple[dict, dict]]:
    """从题库分层抽样（按文件/模板分层），返回 (question, case) 列表。"""
    papers_root = (
        Path(__file__).resolve().parents[1]
        / "backend/competition_app/data/qualification_papers/papers"
    )
    files = sorted(papers_root.glob("*.json"))
    if not files:
        raise FileNotFoundError(f"题库目录为空: {papers_root}")
    all_questions: list[tuple[str, dict]] = []
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        for q in data.get("questions", []):
            if q.get("question_type") == "single_choice" and q.get("options") and q.get("answer"):
                all_questions.append((f.stem, q))
    rng = random.Random(seed)
    rng.shuffle(all_questions)
    # 按模板分层：每个文件最多取 ceil(sample/文件数)，保证类别覆盖
    per_file = max(1, (sample_size + len(files) - 1) // len(files))
    buckets: dict[str, list[tuple[str, dict]]] = {}
    for stem, q in all_questions:
        buckets.setdefault(stem, []).append((stem, q))
    sampled: list[tuple[str, dict]] = []
    for stem in sorted(buckets):
        sampled.extend(buckets[stem][:per_file])
    sampled = sampled[:sample_size]
    cases = []
    for i, (stem, q) in enumerate(sampled):
        cases.append(
            {
                "case_id": f"halu_{stem[:8]}_{q.get('question_id', i)}",
                "user_request": build_user_request(q),
                "expected": q.get("answer", ""),  # 题库格式 ['C']
                "meta": {
                    "question_id": q.get("question_id"),
                    "template": stem,
                    "source_ref": q.get("source_ref"),
                },
            }
        )
    return [(q, c) for (_, q), c in zip(sampled, cases)]


async def judge_case(judge: LLMJudge, record: dict) -> dict:
    """双通道判定：通道A 字母比对；通道B LLM Judge（2 次多数决）。"""
    expected = record["input"].get("expected") or ""
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

    # 通道 A：字母比对
    a_match = answer_letter_match(system_output, expected)
    # 通道 B：LLM Judge
    user = f"题目：{record['input'].get('user_request', '')}\n正确答案：{expected}\n系统回答：{system_output or '(空)'}"
    results = await judge.judge(JUDGE_SYSTEM, user)
    verdict, votes = judge.majority(results, "is_correct")
    halu_votes = [r.get("halu") for r in results if isinstance(r.get("halu"), bool)]
    halu_verdict = any(halu_votes) if halu_votes else None

    is_correct = a_match if a_match is not None else (verdict == "True")
    halu = halu_verdict
    halu_types = sorted({t for r in results for t in (r.get("halu_types") or [])})
    conflict = (
        a_match is not None and halu is not None and (a_match != (not halu))
    )
    return {
        "case_id": record["case_id"],
        "channel_a_match": a_match,
        "channel_b_correct": verdict,
        "channel_b_votes": votes,
        "halu": halu,
        "halu_types": halu_types,
        "is_correct": is_correct,
        "conflict": conflict,
        "reason": (results[0].get("reason") if results else ""),
    }


async def run(args: argparse.Namespace) -> None:
    questions_cases = load_questions(args.limit or args.sample, args.seed)
    cases = [c for _, c in questions_cases]
    if not cases:
        raise SystemExit("抽样为空")

    config = RunConfig(
        output_dir=Path(__file__).parent / "outputs",
        workers=args.workers,
        learner_id=args.learner,
    )
    if args.env:
        config.env_file = Path(args.env)
    runner = EvaluationRunner(config)
    runner.build_container()
    print(f"[codex1] 开始执行 {len(cases)} 题…（每题约 1-3 分钟）", file=sys.stderr)
    out_path = await runner.run_cases(cases, run_name=f"hallucination_{args.run_tag}")

    # 判定
    judge = LLMJudge()
    records = load_jsonl(out_path)
    judged: list[dict] = []
    for rec in records:
        if rec.get("skipped"):
            continue
        judgement = await judge_case(judge, rec)
        judged.append({**rec, **judgement})

    # 统计
    total = len(judged)
    done = [r for r in judged if r.get("status") == "success"]
    sys_out = [r for r in done if r.get("audit", {}).get("decision") == "pass"]
    halu_model = [r for r in judged if r.get("halu")]
    halu_sys = [r for r in sys_out if r.get("halu")]
    correct = [r for r in judged if r.get("is_correct")]
    rejections = [r for r in done if r.get("audit", {}).get("decision") != "pass"]

    sections = [
        ("执行概况", f"- 总用例: {total}\n- 成功: {len(done)}\n- 审核拦截: {len(rejections)}"),
        ("模型级幻觉率（Expert 输出）", render_proportion("幻觉用例", len(halu_model), total, 0.05)),
        ("系统级幻觉率（审核放行后）", render_proportion("放行幻觉", len(halu_sys), len(sys_out) or 1, 0.05)),
        ("客观题准确率（对照）", render_proportion("答对", len(correct), total, 0.85)),
        ("幻觉类型分布", "\n".join(f"- {t}: {len([r for r in halu_model if t in r.get('halu_types', [])])}" for t in HALU_TYPES)),
        ("冲突/待人工复核", f"- 双通道冲突: {len([r for r in judged if r.get('conflict')])}\n- 拒答/无输出: {len([r for r in judged if not (r.get('resource') or {}).get('content')])}"),
    ]
    report = Path(__file__).parent / "outputs" / f"hallucination_report_{args.run_tag}.md"
    write_report(report, "幻觉率测试报告", sections)

    # 失败案例清单
    fails = [r for r in halu_model][:50]
    if fails:
        lines = ["| case_id | 幻觉类型 | snapshot |", "|---|---|---|"]
        for r in fails:
            lines.append(f"| {r['case_id']} | {','.join(r.get('halu_types', []))} | {r.get('snapshot_path')} |")
        write_report(
            report.parent / f"hallucination_failures_{args.run_tag}.md",
            "失败案例清单（可回放追责）",
            [("案例", "\n".join(lines))],
        )


def main() -> None:
    p = argparse.ArgumentParser(description="幻觉率测试")
    p.add_argument("--sample", type=int, default=1000, help="抽样题数（默认 1000）")
    p.add_argument("--limit", type=int, default=None, help="小批量冒烟（如 20）")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--workers", type=int, default=3)
    p.add_argument("--learner", default="eval-halu-user")
    p.add_argument("--env", default=None, help="env 文件路径（含 API keys）")
    p.add_argument("--run-tag", default=None, help="运行标识，默认时间戳")
    args = p.parse_args()
    import time

    args.run_tag = args.run_tag or time.strftime("%Y%m%d_%H%M%S")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
