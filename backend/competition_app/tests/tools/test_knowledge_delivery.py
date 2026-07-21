import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from competition_app.tools.knowledge_delivery import (
    KnowledgeDeliveryBackend,
    KnowledgeDeliveryPaths,
)


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def build_backend(tmp_path: Path) -> KnowledgeDeliveryBackend:
    handoff = tmp_path / "handoff"
    component = handoff / "知识库管理组件"
    public = component / "data" / "backend_delivery"
    source_file = public / "03_pipeline_chunks" / "source_chunks.jsonl"
    write_jsonl(
        source_file,
        [
            {
                "chunk_uid": "C_1",
                "book": "方剂学",
                "kp_Lv1": "方剂学",
                "kp_Lv2": "补益剂",
                "text": "四君子汤原始教材内容。",
                "retrieval_text": "四君子汤由人参、白术、茯苓、甘草组成，功用益气健脾。",
                "metadata": {"heading_path": ["补益剂", "四君子汤"]},
            }
        ],
    )
    write_json(
        public / "04_knowledge_points" / "final_knowledge_points.json",
        [
            {
                "kp": {
                    "kp_id": "KP_1",
                    "kp_lv1": "方剂学",
                    "kp_lv2": "补益剂",
                    "kp_lv3": "四君子汤",
                    "other_name": "四君子",
                    "order": "1",
                    "raw_content": ["C_1"],
                    "exam_bridges": [],
                }
            }
        ],
    )
    write_json(
        public / "01_question_bank" / "formatted_questions.json",
        [
            {
                "question_id": "Q_PUBLIC",
                "question_type": "多项选择题",
                "question_content": "四君子汤包含哪些药？",
                "options": [
                    {"option_id": "A", "content": "人参"},
                    {"option_id": "B", "content": "白术"},
                ],
                "answer": ["A", "B"],
                "explanation": "两者均属于四君子汤。",
                "difficulty": "基础",
                "kp_ids": ["KP_1"],
                "tokenized_content": ["四君子汤", "人参", "白术"],
            }
        ],
    )
    write_jsonl(public / "07_exam_bridge" / "kp_exam_matches.jsonl", [])
    (public / "08_exam_learning_path_2025").mkdir(parents=True, exist_ok=True)
    (component / "retrieval").mkdir(parents=True, exist_ok=True)
    # Validation checks the shipped module path; tests use the real module via sys.path.
    (component / "retrieval" / "hybrid_question_retrieval.py").touch()
    video_root = handoff / "bilibili_video_page" / "runtime" / "full_batch_results"
    write_json(video_root / "catalog.json", {"count": 1})
    write_json(
        video_root / "BV_TEST" / "classification_result.json",
        {
            "bvid": "BV_TEST",
            "video_title": "方剂学课程",
            "pages": [
                {
                    "page": 1,
                    "original_part_title": "四君子汤",
                    "segments": [
                        {
                            "start_seconds": 12,
                            "end_seconds": 42,
                            "topic": "组成",
                            "transcript": "讲解四君子汤组成。",
                            "kp_matches": [{"kp_id": "KP_1", "confidence": 0.91}],
                        }
                    ],
                }
            ],
        },
    )
    paths = KnowledgeDeliveryPaths.from_handoff_root(
        handoff,
        runtime_root=component / "runtime",
        public_vector_store=tmp_path / "vdb_store",
    )
    # The implementation modules are loaded from the real handoff package while
    # all data and runtime writes remain isolated in this fixture.
    real_component = (
        Path(__file__).resolve().parents[3]
        / "competition"
        / "知识星球视频知识库_前端交接包_2026-07-18"
        / "知识库管理组件"
    )
    paths = KnowledgeDeliveryPaths(
        component_root=real_component,
        public_data=paths.public_data,
        video_results=paths.video_results,
        runtime_root=paths.runtime_root,
        public_vector_store=paths.public_vector_store,
    )
    return KnowledgeDeliveryBackend(
        paths,
        embedding_base_url="https://example.test/v1",
        embedding_model="test-embedding",
    )


@pytest.mark.asyncio
async def test_delivery_evidence_preserves_retrieval_text_and_local_video(tmp_path: Path) -> None:
    backend = build_backend(tmp_path)

    pack = await backend.build_local_evidence_pack("四君子汤")

    assert {route["id"] for route in backend.map.routes()} == {
        "textbook_14_5",
        "tcm_assistant",
        "postgraduate",
    }
    assert pack.resolved_kp_ids == ["KP_1"]
    textbook = next(item for item in pack.evidence_items if item.resource_type == "textbook")
    video = next(item for item in pack.evidence_items if item.resource_type == "video")
    assert "益气健脾" in textbook.content_summary
    assert video.source_url == "https://www.bilibili.com/video/BV_TEST?p=1&t=12"
    detail = backend.map.detail("KP_1")
    assert detail["chunks"][0]["text"] == "四君子汤原始教材内容。"
    assert detail["chunks"][0]["retrieval_text"] != detail["chunks"][0]["text"]


def test_topic_resolution_accepts_model_query_with_qualifiers(tmp_path: Path) -> None:
    backend = build_backend(tmp_path)
    backend.map.ensure_hierarchy()
    backend.map.kps.update(
        {
            "KP_2": {
                "kp_id": "KP_2",
                "kp_lv1": "方剂学",
                "kp_lv2": "温里剂",
                "kp_lv3": "理中丸",
                "raw_content": [],
            },
            "KP_3": {
                "kp_id": "KP_3",
                "kp_lv1": "中医基础",
                "kp_lv2": "病机",
                "kp_lv3": "病机",
                "raw_content": [],
            },
        }
    )

    matches = backend.map.resolve_topic("四君子汤 人参 君药 方义；理中丸 核心区别 病机")

    assert [item["kp_id"] for item in matches[:2]] == ["KP_1", "KP_2"]


def test_exam_adapter_reads_nested_customer_delivery(tmp_path: Path) -> None:
    path = (
        tmp_path
        / "U1"
        / "TCM_backend_delivery"
        / "04_knowledge_points"
        / "final_knowledge_points.json"
    )
    write_json(path, [{"kp": {"kp_id": "USER_KP_1"}}])
    module = SimpleNamespace(
        read_json=lambda target, default: json.loads(target.read_text(encoding="utf-8"))
        if target.is_file()
        else default
    )

    KnowledgeDeliveryBackend._patch_exam_user_kp_layout(module)
    resolved_path, rows = module.load_user_kps(tmp_path, "U1")

    assert resolved_path == path
    assert rows[0]["kp"]["kp_id"] == "USER_KP_1"


@pytest.mark.asyncio
async def test_binary_imports_are_owner_scoped_and_require_mineru_for_pdf(tmp_path: Path) -> None:
    backend = build_backend(tmp_path)

    saved = backend._save_upload("../notes.md", b"# notes", "U1", {".md"})

    assert saved.parent == backend.paths.runtime_root / "uploads" / "U1"
    assert saved.name.endswith("_notes.md")
    with pytest.raises(ValueError, match="MinerU"):
        await backend.ingest_exam_file("exam.pdf", b"%PDF", "U1")
    with pytest.raises(ValueError, match="文件类型"):
        backend._save_upload("payload.exe", b"x", "U1", {".md"})


@pytest.mark.asyncio
async def test_question_schema_keeps_answer_options_and_owner_isolation(tmp_path: Path) -> None:
    backend = build_backend(tmp_path)
    runtime = backend.paths.question_runtime
    write_jsonl(
        runtime / "question_events.jsonl",
        [
            {
                "question_id": "Q_U1",
                "status": "active",
                "scope": "user",
                "owner_id": "U1",
                "question_type": "简答题",
                "stem": "U1 的四君子汤题",
                "answer": "U1答案",
                "analysis": "U1解析",
                "kp_ids": ["KP_1"],
            },
            {
                "question_id": "Q_U2",
                "status": "active",
                "scope": "user",
                "owner_id": "U2",
                "question_type": "简答题",
                "stem": "U2 的四君子汤题",
                "answer": "U2答案",
                "analysis": "U2解析",
                "kp_ids": ["KP_1"],
            },
        ],
    )

    result = await backend.search_questions(
        "四君子汤", ["KP_1"], owner_id="U1", scope="all", limit=10
    )

    ids = {item.question_id for item in result.items}
    assert "Q_PUBLIC" in ids
    assert "Q_U1" in ids
    assert "Q_U2" not in ids
    public = next(item for item in result.items if item.question_id == "Q_PUBLIC")
    assert public.reference_answer == "A, B"
    assert public.options == ["A. 人参", "B. 白术"]
    assert public.source_metadata["raw_answer"] == ["A", "B"]
