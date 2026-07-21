from __future__ import annotations

from pathlib import Path

from APP.backend.data_import_service import convert_chunks_to_markdown, load_knowledge_points, load_questions

ROOT = Path(__file__).resolve().parents[2]
TEST_DATA = ROOT / "test_data"
PUBLIC_DATA = ROOT / "backend" / "data"


def main() -> None:
    kp_rows = load_knowledge_points(TEST_DATA / "知识点数据库的数据集.json")
    question_rows = load_questions(TEST_DATA / "题库结构.json")
    chunk_count = convert_chunks_to_markdown(
        TEST_DATA / "切片数据_刺法灸法学_clean_identifier_chunks.jsonl",
        PUBLIC_DATA / "刺法灸法学_clean.md",
    )
    print({"knowledge_points": len(kp_rows), "questions": len(question_rows), "chunks": chunk_count})


if __name__ == "__main__":
    main()
