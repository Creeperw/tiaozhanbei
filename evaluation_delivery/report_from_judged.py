# -*- coding: utf-8 -*-
"""从已判定 JSONL 直接生成 CMB 报告（不重跑执行、不重新判定）。

用法：
  python report_from_judged.py --judged outputs/cmb_550_rejudged_v11.jsonl --run-tag 550_rejudged_v11
"""
import argparse
import json
from pathlib import Path
from types import SimpleNamespace

from codex1_hallucination import _write_cmb_report


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--judged", type=Path, required=True)
    p.add_argument("--run-tag", required=True)
    args = p.parse_args()
    rows = [
        json.loads(line)
        for line in args.judged.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    _write_cmb_report(SimpleNamespace(run_tag=args.run_tag), rows)


if __name__ == "__main__":
    main()
