from __future__ import annotations

"""Render one internal D1 AB100 model-I/O trace as a compact handoff sample.

The JSON remains the complete machine-readable trajectory.  The Markdown
follows the project's existing "input/output compact edition" convention and
links back to that JSON instead of duplicating every prompt field inline.
"""

import argparse
import json
from pathlib import Path
from typing import Any


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render a D1 AB100 trace sample")
    parser.add_argument("trace", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def _preview(value: Any, limit: int = 1800) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n\n……（完整内容见同目录 JSON）"


def _bool(value: Any) -> str:
    return "是" if value is True else "否" if value is False else "—"


def render(trace: dict[str, Any], *, snapshot_name: str) -> str:
    case = trace["case"]
    result = trace["pair_result"]
    validation = result["validation"]
    arms = result["arms"]
    model_calls = trace.get("model_calls") or []
    technical_errors = [item for item in model_calls if item.get("error_type")]
    binding = result.get("conflict_binding") or {}
    evidence_pair = binding.get("evidence_pair") or {}

    call_rows = []
    for item in model_calls:
        duration = item.get("total_duration_ms")
        duration_text = f"{duration / 1000:.1f}s" if isinstance(duration, int) else "—"
        status = f"失败：{item['error_type']}" if item.get("error_type") else "成功"
        call_rows.append(
            "| {sequence} | `{agent}` | {status} | {input_chars} | {output_chars} | {duration} |".format(
                sequence=item.get("sequence", "—"),
                agent=item.get("agent", "unknown"),
                status=status,
                input_chars=item.get("input_chars", 0),
                output_chars=item.get("output_chars", 0),
                duration=duration_text,
            )
        )

    arm_rows = []
    for name in ("A", "B"):
        arm = arms[name]
        arm_rows.append(
            "| {name} | {rule} | `{audit}` | {failure} | {same_pair} | {closure} | {rerun} |".format(
                name=name,
                rule=_bool(arm.get("rule_exposed")),
                audit=arm.get("audit_decision"),
                failure=_bool(arm.get("target_failure")),
                same_pair=_bool(arm.get("same_conflict_pair_resolved")),
                closure=_bool(arm.get("closure_allowed")),
                rerun=" → ".join(arm.get("actual_rerun_step_ids") or []) or "无",
            )
        )

    b_arm = arms["B"]
    recovered = bool(
        technical_errors
        and validation.get("valid") is True
        and b_arm.get("closure_allowed") is True
        and b_arm.get("audit_decision") == "pass"
        and b_arm.get("same_conflict_pair_resolved") is True
    )
    if recovered:
        technical_note = (
            f"本次出现 **{len(technical_errors)} 次中间模型调用失败**，随后在同次隔离执行中恢复；"
            "最终B组审核和闭环门禁均通过，可计入正式结果，但应另记一次技术重试。"
        )
    elif technical_errors:
        technical_note = (
            f"本次存在 **{len(technical_errors)} 次模型调用技术失败**且最终链路未恢复，"
            "因此只作为完整轨迹诊断样本，**不进入正式效果指标分母**。"
        )
    else:
        technical_note = "本次未发现模型调用技术失败，可在其他正式门禁同时通过后计入效果指标。"
    return f"""# D1 AB100·输入输出精简版（1条）

> 本文只保留输入、关键中间过程和A/B输出摘要；完整模型请求、模型原始输出及配对结果见同目录 `{snapshot_name}`。隐藏推理未导出。

## 语义冲突闭环 · {case['case_id']}

**快照**：`{snapshot_name}`  
**任务**：`{case['expected_task_type']}`  
**分组**：`{case['case_group']}`  
**执行顺序**：`{case['pair_order']}`  
**模型**：`{trace.get('model_name')}`  
**开始时间**：`{trace.get('started_at')}`  
**结束时间**：`{trace.get('finished_at')}`

### ① 输入

> **用户请求**：{case['prompt']}

> **隔离约束**：A/B使用同一冻结证据上下文；不写正式会话、学习计划、用户记忆或业务数据库。

### ② 冻结上下文与故障注入

- 上下文一致：**{_bool(validation.get('context_equal'))}**
- 检索包摘要：`{result.get('retrieval_pack_digest')}`
- 冲突声明位置：`{binding.get('claim_location')}`
- 支持侧证据：`{evidence_pair.get('support_evidence_id')}`
- 冲突侧证据：`{evidence_pair.get('conflict_evidence_id')}`
- 责任节点：`{binding.get('owner_step_id')}`
- A组规则曝光次数：**{validation.get('a_rule_exposure')}**
- B组仅向目标Agent曝光：**{_bool(validation.get('b_target_rule_exposure'))}**

### ③ 模型调用轨迹

| 序号 | Agent | 调用状态 | 输入字符 | 输出字符 | 耗时 |
|---:|---|---|---:|---:|---:|
{chr(10).join(call_rows)}

### ④ A/B结果对照

| 组别 | 规则曝光 | 最终Audit | 目标故障 | 同一冲突证据对闭环 | D1闭环门禁 | 节点链标记 |
|---|---|---|---|---|---|---|
{chr(10).join(arm_rows)}

### ⑤ A组输出开头（规则关闭）

> {_preview(arms['A'].get('user_output')).replace(chr(10), chr(10) + '> ')}

### ⑥ B组输出开头（规则开启）

> {_preview(arms['B'].get('user_output')).replace(chr(10), chr(10) + '> ')}

### ⑦ 本条结论

- 配对回执结构有效：**{_bool(validation.get('valid'))}**
- B组记录的局部返修节点链：`{' → '.join(arms['B'].get('actual_rerun_step_ids') or []) or '无'}`
- B组最终Audit：`{arms['B'].get('audit_decision')}`
- B组闭环放行：**{_bool(arms['B'].get('closure_allowed'))}**
- 新增无证据声明：**{_bool(arms['B'].get('new_unsupported_claims'))}**
- {technical_note}

> 注意：`validation.valid=true`只说明配对结构、上下文一致性和规则曝光门禁有效，不代表模型调用全部成功，也不代表B组审核通过。A组未启用D1规则，其`closure_allowed`只表示闭环规则没有拦截，不能解释为审核通过或允许发布。
"""


def main() -> None:
    args = _args()
    trace = json.loads(args.trace.read_text(encoding="utf-8"))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    snapshot = args.output_dir / args.trace.name
    snapshot.write_bytes(args.trace.read_bytes())
    markdown = args.output_dir / "输入输出精简版.md"
    markdown.write_text(render(trace, snapshot_name=snapshot.name), encoding="utf-8")
    print(json.dumps({"snapshot": str(snapshot), "markdown": str(markdown)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
