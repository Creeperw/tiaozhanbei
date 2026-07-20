from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def _digits(value: str) -> str:
    match = re.search(r"(\d+)$", str(value))
    return match.group(1) if match else re.sub(r"\W+", "_", str(value)).strip("_")


def normalize_kp_id(raw_id: str, namespace: str = "KP_TCM") -> str:
    suffix = _digits(raw_id).zfill(6)
    return f"{namespace}_{suffix}"


def normalize_chunk_id(raw_id: str) -> str:
    return f"CHUNK_ACU_MOXA_{_digits(raw_id).zfill(5)}"


def normalize_question_id(raw_id: str) -> str:
    return f"Q_TCM_PED_{_digits(raw_id).zfill(6)}"


def load_knowledge_points(path: Path) -> list[dict[str, Any]]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    return [
        {
            "kp_id": normalize_kp_id(item.get("kp_id", "")),
            "name": item.get("kp_Lv3_standard") or item.get("kp_id"),
            "aliases": [item.get("kp_Lv3_others")] if item.get("kp_Lv3_others") else [],
            "description": f"{item.get('kp_Lv1', '')} / {item.get('kp_Lv2', '')}",
            "source": "test_data:知识点数据库的数据集.json",
            "resource_type": "knowledge_point",
        }
        for item in rows
    ]


def load_questions(path: Path) -> list[dict[str, Any]]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    return [
        {
            "question_id": normalize_question_id(item.get("题目id", "")),
            "source_original_id": item.get("题目id", ""),
            "stem": item.get("题目内容", ""),
            "answer": item.get("题目答案", ""),
            "question_type": item.get("题型", "single_choice"),
            "source": item.get("题目大来源", "test_data:题库结构.json"),
            "resource_type": "question",
            "kp_ids": [],
        }
        for item in rows
    ]


def _heading_from_item(item: dict[str, Any], metadata: dict[str, Any], fallback: str) -> str:
    heading_path = item.get("heading_path")
    if isinstance(heading_path, list):
        return " / ".join(str(part) for part in heading_path if part).strip(" / ") or fallback
    metadata_heading = metadata.get("heading_path")
    if isinstance(metadata_heading, str):
        return metadata_heading.strip() or fallback
    if isinstance(metadata_heading, list):
        return " / ".join(str(part) for part in metadata_heading if part).strip(" / ") or fallback
    return fallback


def convert_chunks_to_markdown(input_path: Path, output_path: Path) -> int:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    lines: list[str] = ["# 刺法灸法学 clean 切片\n"]
    with input_path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            if not raw_line.strip():
                continue
            item = json.loads(raw_line)
            raw_chunk_id = str(item.get("chunk_id", ""))
            suffix = _digits(raw_chunk_id).zfill(5)
            chunk_id = normalize_chunk_id(raw_chunk_id)
            source_id = f"KB_ACU_MOXA:{suffix}"
            metadata = item.get("metadata") or {}
            heading = _heading_from_item(item, metadata, chunk_id)
            lines.extend([
                f"\n## {heading}\n",
                f"source_id: {source_id}\n",
                f"resource_id: {chunk_id}\n",
                "resource_type: knowledge_chunk\n",
                f"kp_lv1: {metadata.get('kp_Lv1', '')}\n",
                f"kp_lv2: {metadata.get('kp_Lv2', '')}\n",
                "\n",
                item.get("text", "").strip(),
                "\n",
            ])
            count += 1
    output_path.write_text("\n".join(lines), encoding="utf-8")
    return count
