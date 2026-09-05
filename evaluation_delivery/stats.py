"""统计工具：比例、Wilson 95% 置信区间、报告渲染。"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Iterable


def wilson_ci(success: int, total: int, z: float = 1.96) -> tuple[float, float, float]:
    """返回 (点估计, 下界, 上界)。total=0 时返回 (0, 0, 0)。"""
    if total <= 0:
        return (0.0, 0.0, 0.0)
    p = success / total
    denom = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denom
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denom
    lo = max(0.0, center - margin)
    hi = min(1.0, center + margin)
    return (p, lo, hi)


def render_proportion(label: str, success: int, total: int, target: float | None = None) -> str:
    p, lo, hi = wilson_ci(success, total)
    line = f"- {label}: {success}/{total} = {p:.1%} (95% CI {lo:.1%}~{hi:.1%})"
    if target is not None:
        passed = "✅ 达标" if p >= target else "❌ 未达标"
        line += f"  目标 ≥{target:.0%} {passed}"
    return line


def render_proportion_lower_better(label: str, success: int, total: int, target: float | None = None) -> str:
    """反向目标口径：比例越低越好（如幻觉率 <5%）。"""
    p, lo, hi = wilson_ci(success, total)
    line = f"- {label}: {success}/{total} = {p:.1%} (95% CI {lo:.1%}~{hi:.1%})"
    if target is not None:
        passed = "✅ 达标" if p <= target else "❌ 未达标"
        line += f"  目标 ≤{target:.0%} {passed}"
    return line


def write_report(out_path: Path, title: str, sections: list[tuple[str, str]]) -> None:
    """渲染 markdown 报告（自然语言优先，结构化数字只保留必要项）。"""
    lines = [f"# {title}", ""]
    for heading, body in sections:
        lines.append(f"## {heading}")
        lines.append("")
        lines.append(body.strip())
        lines.append("")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[report] 已写入 {out_path}")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def count_by(rows: Iterable[dict[str, Any]], key: str) -> dict[str, int]:
    from collections import Counter

    return dict(Counter(str(r.get(key)) for r in rows))
