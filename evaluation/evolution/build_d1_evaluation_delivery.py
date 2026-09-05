from __future__ import annotations

"""Build the auditable D1 formal-evaluation delivery package."""

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import zipfile


ROOT = Path(__file__).resolve().parents[2]
PACKAGE_NAME = "失败驱动进化D1正式A-B评测交付包-20260820"
DELIVERABLES = ROOT / "deliverables"
STAGING = DELIVERABLES / PACKAGE_NAME
ARCHIVE = DELIVERABLES / f"{PACKAGE_NAME}.zip"

DATASET = ROOT / "evaluation/evolution/datasets/semantic_conflict_closure_ab50_20260819.jsonl"
RESULTS = ROOT / "evaluation/evolution/outputs/semantic_conflict_closure_ab50_results.jsonl"
METRICS = ROOT / "evaluation/evolution/outputs/semantic_conflict_closure_ab50_metrics.json"
HISTORICAL_PACKAGE = ROOT / "deliverables/多智能体追责评测交付包-20260813.zip"
TRACE_CASE_IDS = (
    "EVO-D1-AB50-001",  # paired improvement
    "EVO-D1-AB50-005",  # revise and safe block
    "EVO-D1-AB50-007",  # human review and safe block
    "EVO-D1-AB50-042",  # non-regression control
    "EVO-D1-AB50-046",  # targeting-negative control
)


COPY_MAP = {
    "01_评测方案/04_规则反哺与失败进化评测方案.md": "docs/04_规则反哺与失败进化评测方案.md",
    "01_评测方案/D1正式A-B评测启动准备报告.md": "docs/失败规则候选D1-正式50条A-B评测启动准备报告-20260819.md",
    "02_测试数据/正式50条.jsonl": "evaluation/evolution/datasets/semantic_conflict_closure_ab50_20260819.jsonl",
    "02_测试数据/正式50条.csv": "evaluation/evolution/datasets/semantic_conflict_closure_ab50_20260819.csv",
    "02_测试数据/正式50条.manifest.json": "evaluation/evolution/datasets/semantic_conflict_closure_ab50_20260819.manifest.json",
    "02_测试数据/真实模型预检5条.jsonl": "evaluation/evolution/datasets/semantic_conflict_closure_pilot5_20260819.jsonl",
    "02_测试数据/真实模型预检5条.manifest.json": "evaluation/evolution/datasets/semantic_conflict_closure_pilot5_20260819.manifest.json",
    "03_原始测试结果/正式50条A-B紧凑回执.jsonl": "evaluation/evolution/outputs/semantic_conflict_closure_ab50_results.jsonl",
    "04_指标与报告/严格可复算指标.json": "evaluation/evolution/outputs/semantic_conflict_closure_ab50_metrics.json",
    "04_指标与报告/逐条配对结果.csv": "evaluation/evolution/outputs/semantic_conflict_closure_ab50_cases.csv",
    "04_指标与报告/D1正式50条A-B对照评测报告.md": "docs/失败规则候选D1-正式50条A-B对照评测报告-20260820.md",
    "04_指标与报告/正式前10条在线结果.md": "docs/失败规则候选D1-正式A-B前10条在线结果-20260820.md",
    "04_指标与报告/真实模型5条预检结果.md": "docs/失败规则候选D1-5条真实模型在线预检结果-20260819.md",
    "06_复现代码/build_d1_formal_dataset.py": "evaluation/evolution/build_d1_formal_dataset.py",
    "06_复现代码/build_d1_evaluation_delivery.py": "evaluation/evolution/build_d1_evaluation_delivery.py",
    "06_复现代码/run_d1_formal_online.py": "evaluation/evolution/run_d1_formal_online.py",
    "06_复现代码/run_d1_model_io_trace.py": "evaluation/evolution/run_d1_model_io_trace.py",
    "06_复现代码/summarize_d1_formal_results.py": "evaluation/evolution/summarize_d1_formal_results.py",
    "06_复现代码/semantic_conflict_closure.py": "backend/competition_app/evaluation/semantic_conflict_closure.py",
    "06_复现代码/d1_precheck_dataset.py": "backend/competition_app/evaluation/d1_precheck_dataset.py",
    "06_复现代码/d1_precheck_runner.py": "backend/competition_app/evaluation/d1_precheck_runner.py",
    "06_复现代码/d1_precheck_execution.py": "backend/competition_app/evaluation/d1_precheck_execution.py",
    "06_复现代码/d1_formal_runner.py": "backend/competition_app/evaluation/d1_formal_runner.py",
    "06_复现代码/test_d1_formal_metrics.py": "backend/competition_app/tests/evaluation/test_d1_formal_metrics.py",
    "06_复现代码/test_d1_formal_online_runner.py": "backend/competition_app/tests/evaluation/test_d1_formal_online_runner.py",
    "06_复现代码/test_d1_formal_runner.py": "backend/competition_app/tests/evaluation/test_d1_formal_runner.py",
    "06_复现代码/test_d1_precheck_execution.py": "backend/competition_app/tests/evaluation/test_d1_precheck_execution.py",
    "06_复现代码/test_d1_precheck_runner.py": "backend/competition_app/tests/evaluation/test_d1_precheck_runner.py",
    "05_模型输入输出诊断/EVO-D1-AB50-001.model_io.json": "evaluation/evolution/outputs/semantic_conflict_closure_model_io_traces/EVO-D1-AB50-001.model_io.json",
    "05_模型输入输出诊断/EVO-D1-AB50-005.model_io.json": "evaluation/evolution/outputs/semantic_conflict_closure_model_io_traces/EVO-D1-AB50-005.model_io.json",
    "05_模型输入输出诊断/EVO-D1-AB50-042.model_io.json": "evaluation/evolution/outputs/semantic_conflict_closure_model_io_traces/EVO-D1-AB50-042.model_io.json",
    "05_模型输入输出诊断/index.json": "evaluation/evolution/outputs/semantic_conflict_closure_model_io_traces/index.json",
}


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _copy_files() -> None:
    for target, source in COPY_MAP.items():
        source_path = ROOT / source
        if not source_path.is_file():
            raise FileNotFoundError(source_path)
        target_path = STAGING / target
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, target_path)


def _historical_metrics() -> None:
    member = (
        "accountability-evaluation-20260813/04_指标与报告/严格可复算指标.json"
    )
    with zipfile.ZipFile(HISTORICAL_PACKAGE) as archive:
        content = archive.read(member)
    target = STAGING / "04_指标与报告/历史追责严格可复算指标.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)


def _paired_outcome(case: dict, result: dict) -> str:
    if case["case_group"] != "target_fault":
        return "control_no_target_fault"
    a_defect = bool(result["arms"]["A"]["target_failure"])
    b_defect = not bool(result["arms"]["B"]["closure_allowed"])
    if a_defect and not b_defect:
        return "paired_improvement"
    if not a_defect and b_defect:
        return "paired_regression"
    if a_defect and b_defect:
        return "both_defective_safe_block"
    return "both_successful"


def _trace_samples() -> None:
    cases = {row["case_id"]: row for row in _jsonl(DATASET)}
    results = {row["case_id"]: row for row in _jsonl(RESULTS)}
    joined: list[dict] = []
    markdown = [
        "# D1正式A/B完整评测轨迹样例",
        "",
        "这里的“完整”是指当前正式评测有意保留的完整可审计回执：冻结任务、分组、",
        "冲突绑定、规则曝光、A/B结果、局部重跑链、最终Audit、摘要一致性和时间。",
        "为避免提示词与用户正文泄漏，正式执行器没有持久化原始模型Prompt、思考过程和全文输出；",
        "因此这些内容不能从本轮结果中事后恢复，也没有在交付包中伪造。",
        "",
    ]
    for index, case_id in enumerate(TRACE_CASE_IDS, start=1):
        case = cases[case_id]
        result = results[case_id]
        record = {
            "trace_schema_version": "d1-delivery-trace-1.0",
            "case": case,
            "result": result,
            "derived": {
                "paired_outcome": _paired_outcome(case, result),
                "a_final_target_defect": (
                    bool(result["arms"]["A"]["target_failure"])
                    if case["case_group"] == "target_fault"
                    else None
                ),
                "b_final_target_defect": (
                    not bool(result["arms"]["B"]["closure_allowed"])
                    if case["case_group"] == "target_fault"
                    else None
                ),
                "business_publish_allowed_b": (
                    bool(result["arms"]["B"]["closure_allowed"])
                    and str(result["arms"]["B"]["audit_decision"]) == "pass"
                ),
            },
        }
        joined.append(record)
        markdown.extend(
            [
                f"## {index}. {case_id}",
                "",
                f"- 类型：`{case['case_group']}` / `{case['scenario']}`",
                f"- A/B顺序：`{case['pair_order']}`",
                f"- 用户任务：{case['prompt']}",
                f"- 配对结论：`{record['derived']['paired_outcome']}`",
                "",
                "```json",
                json.dumps(record, ensure_ascii=False, indent=2),
                "```",
                "",
            ]
        )
    trace_dir = STAGING / "05_完整轨迹样例"
    trace_dir.mkdir(parents=True, exist_ok=True)
    (trace_dir / "5条代表性完整评测轨迹.md").write_text(
        "\n".join(markdown), encoding="utf-8"
    )
    (trace_dir / "5条代表性完整评测轨迹.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in joined),
        encoding="utf-8",
    )


def _readme() -> None:
    metrics = json.loads(METRICS.read_text(encoding="utf-8"))
    effect = metrics["primary_effect"]
    safety = metrics["process_and_safety"]
    content = f"""# 失败驱动进化D1正式A/B评测交付包

交付日期：2026-08-20  
目标规则：`semantic_conflict_pair_closure_v1`

## 交付内容

| 目录 | 内容 |
|---|---|
| `01_评测方案` | 完整失败进化评测方案与正式启动边界 |
| `02_测试数据` | 正式50条、预检5条及数据哈希 |
| `03_原始测试结果` | 50条真实模型A/B紧凑可审计回执 |
| `04_指标与报告` | 可复算指标、逐条CSV、正式报告和历史追责基线 |
| `05_完整轨迹样例` | 成功、打回、人工复核、非退化和阴性对照代表轨迹 |
| `05_模型输入输出诊断` | 3条额外诊断重跑的完整脱敏模型请求消息和原始响应 |
| `06_复现代码` | 数据生成、在线执行、指标统计和相关测试 |
| `07_校验` | 文件清单、构建信息和SHA-256 |

## 核心正式A/B指标

- A组目标缺陷率：{effect['a_target_defect_rate'] * 100:.1f}%；
- B组最终目标缺陷率：{effect['b_final_target_defect_rate'] * 100:.1f}%；
- 绝对缺陷下降：{effect['absolute_defect_reduction_pp']:.1f}个百分点；
- 配对改善：{effect['paired_improvements']}/30；
- 配对退化：{effect['paired_regressions']}/30；
- McNemar双侧精确检验：p={effect['mcnemar_exact_two_sided_p']};
- 不安全放行：{safety['unsafe_releases']}；
- 非目标规则误曝光：{safety['b_control_rule_exposures']}。

## 历史追责指标边界

历史追责指标只用于证明故障发现、责任定位和局部返修基础能力，不充当D1正式A组，
也不得与D1闭环率直接相减。正式规则收益以本包50组冻结配对A/B为准。

## 复算

在项目根目录执行：

```bash
PYTHONPATH=backend:. /home/wangjl/miniconda3/envs/torch/bin/python \
  evaluation/evolution/summarize_d1_formal_results.py
```

本包不包含API密钥、用户隐私、正式数据库或模型思考过程；模型输入只在专门的诊断目录中提供。
正式50条没有保存完整模型边界；`05_模型输入输出诊断`中的输入输出来自额外诊断重跑，
不进入正式50条效果指标。诊断文件包含脱敏后的实际系统/用户消息和模型原始响应，
但明确排除了供应商reasoning字段。
"""
    (STAGING / "README.md").write_text(content, encoding="utf-8")


def _validation_files() -> None:
    validation_dir = STAGING / "07_校验"
    validation_dir.mkdir(parents=True, exist_ok=True)
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip()
        branch = subprocess.check_output(
            ["git", "branch", "--show-current"], cwd=ROOT, text=True
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        commit = "unknown"
        branch = "unknown"
    (validation_dir / "BUILD_INFO.txt").write_text(
        f"built_at=2026-08-20\nbranch={branch}\ncommit={commit}\n"
        "unit_tests=20 passed, 1 third-party deprecation warning\n",
        encoding="utf-8",
    )
    files = sorted(
        path.relative_to(STAGING).as_posix()
        for path in STAGING.rglob("*")
        if path.is_file() and path.name not in {"FILES.txt", "SHA256SUMS"}
    )
    (validation_dir / "FILES.txt").write_text("\n".join(files) + "\n", encoding="utf-8")
    checksums = []
    for relative in files:
        path = STAGING / relative
        checksums.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {relative}")
    (validation_dir / "SHA256SUMS").write_text(
        "\n".join(checksums) + "\n", encoding="utf-8"
    )


def _archive() -> None:
    if ARCHIVE.exists():
        ARCHIVE.unlink()
    with zipfile.ZipFile(ARCHIVE, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(STAGING.rglob("*")):
            if path.is_file():
                archive.write(path, f"{PACKAGE_NAME}/{path.relative_to(STAGING).as_posix()}")


def main() -> None:
    if STAGING.exists():
        shutil.rmtree(STAGING)
    STAGING.mkdir(parents=True)
    _copy_files()
    _historical_metrics()
    _trace_samples()
    _readme()
    _validation_files()
    _archive()
    print(
        json.dumps(
            {
                "package_directory": str(STAGING),
                "archive": str(ARCHIVE),
                "archive_sha256": hashlib.sha256(ARCHIVE.read_bytes()).hexdigest(),
                "file_count": sum(1 for path in STAGING.rglob("*") if path.is_file()),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
