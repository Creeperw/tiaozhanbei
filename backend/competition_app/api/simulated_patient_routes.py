"""
模拟病患 API 路由 — 将 SimulatedPatientEngine 接入 FastAPI 主后端。

所有接口通过 POST /api/v1/simulated-patient 统一调用，
通过请求体中的 action 字段分发到不同处理逻辑。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from competition_app.simulated_patient.engine import SimulatedPatientEngine
from competition_app.simulated_patient.schemas import (
    SimulatedPatientRequest,
    SimulatedPatientResponse,
)
from competition_app.simulated_patient.llm_adapter import ProjectLLMProvider

router = APIRouter(prefix="/api/v1/simulated-patient", tags=["simulated-patient"])

ACUPUNCTURE_CASE_DATA_PATH = Path(__file__).resolve().parents[3] / "docs" / "针灸交互训练-案例数据v1.json"

# ── 引擎引用（由 app.py 在启动时注入） ─────────────────
_engine: Optional[SimulatedPatientEngine] = None


def init_engine(chat_model=None, *, llm_timeout_seconds: float = 120.0) -> SimulatedPatientEngine:
    """由 app.create_app 在初始化时调用。

    chat_model 参数保留以兼容调用方（当前适配器自行读取配置，无需外部注入）。
    """
    global _engine

    data_dir = _get_data_dir()
    case_data_path = _get_case_data_path()

    llm = ProjectLLMProvider(timeout_seconds=llm_timeout_seconds)

    _engine = SimulatedPatientEngine(
        llm_provider=llm,
        test_data=None,
        case_data_path=case_data_path,
        data_dir=str(data_dir),
    )
    return _engine


def _get_data_dir() -> Path:
    runtime_root = Path(__file__).resolve().parents[1] / "runtime" / "simulated_patient"
    runtime_root.mkdir(parents=True, exist_ok=True)
    return runtime_root


def _get_case_data_path() -> str:
    data_dir = Path(__file__).resolve().parents[1] / "data"
    case_path = data_dir / "clinical_cases.json"
    if case_path.is_file():
        return str(case_path)
    legacy = Path(__file__).resolve().parents[3] / "模拟病患5" / "tests" / "clinical_cases.json"
    if legacy.is_file():
        return str(legacy)
    return str(case_path)


def _load_acupuncture_cases() -> list[dict]:
    if not ACUPUNCTURE_CASE_DATA_PATH.is_file():
        return []
    try:
        payload = json.loads(ACUPUNCTURE_CASE_DATA_PATH.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, list):
        return []
    for case in payload:
        case.setdefault("positionTolerance3d", {
            "excellent": 0.012,
            "pass": 0.03,
            "outer": 0.06,
            "unit": "model",
            "metric": "world_euclidean",
            "reviewStatus": "model_calibrated",
        })
    return payload


# ── 请求模型 ──────────────────────────────────────────

class SPRequest(BaseModel):
    user_id: str = Field(default="", max_length=128)
    session_id: str = Field(..., min_length=1, max_length=128)
    action: str = Field(
        ...,
        pattern=r"^(start|dialogue|help|submit|stats|mistakes|collections|history|history_list|clear|reset|dialog_history|history_detail)$",
    )
    user_input: str = Field(default="", max_length=2000)
    case_id: Optional[str] = Field(default=None, max_length=128)
    practice_scope: str = Field(default="full")
    diagnosis: Optional[dict] = Field(default=None)
    help_type: Optional[str] = Field(default=None, pattern=r"^(question|interpretation)$")
    history_id: Optional[str] = Field(default=None, max_length=128)
    limit: int = Field(default=100, ge=1, le=500)


class AcupunctureScoreRequest(BaseModel):
    case_id: str = Field(..., min_length=1, max_length=128)
    needles: list[dict] = Field(default_factory=list)
    standard_positions: dict[str, list[float]] = Field(default_factory=dict)


def _current_engine() -> SimulatedPatientEngine:
    if _engine is None:
        raise RuntimeError("SimulatedPatientEngine not initialized — call init_engine() first")
    return _engine


def _score_acupuncture_case(case_data: dict, needles: list[dict], standard_positions: dict[str, list[float]] | None = None) -> dict:
    del standard_positions  # Retained in the signature only for compatibility with older callers.
    standards = [
        point
        for point in (case_data.get("standardAcupoints") or [])
        if point.get("procedureType") not in {"pricking", "pricking_cupping"}
    ]
    tolerance = case_data.get("positionTolerance3d") or {}
    excellent_tolerance = tolerance.get("excellent")
    pass_tolerance = tolerance.get("pass")
    outer_tolerance = tolerance.get("outer")
    position_configured = (
        isinstance(excellent_tolerance, (int, float))
        and isinstance(pass_tolerance, (int, float))
        and isinstance(outer_tolerance, (int, float))
        and 0 < excellent_tolerance <= pass_tolerance < outer_tolerance
        and bool(standards)
        and all(isinstance(point.get("modelPosition"), list) and len(point["modelPosition"]) == 3 for point in standards)
    )

    def distance(first: list[float], second: list[float]) -> float:
        return sum((first[index] - second[index]) ** 2 for index in range(3)) ** 0.5

    def position_score(value: float) -> int:
        if value <= excellent_tolerance:
            return 100
        if value <= pass_tolerance:
            progress = (value - excellent_tolerance) / (pass_tolerance - excellent_tolerance)
            return round(100 - progress * 40)
        if value <= outer_tolerance:
            progress = (value - pass_tolerance) / (outer_tolerance - pass_tolerance)
            return round(60 - progress * 60)
        return 0

    matches: list[dict] = []
    if position_configured:
        candidates = []
        for standard_index, standard in enumerate(standards):
            for needle_index, needle in enumerate(needles):
                needle_point = needle.get("point")
                if not isinstance(needle_point, list) or len(needle_point) != 3:
                    continue
                try:
                    measured_distance = distance(needle_point, standard["modelPosition"])
                except (TypeError, ValueError):
                    continue
                candidates.append((measured_distance, standard_index, needle_index))
        used_standards: set[int] = set()
        used_needles: set[int] = set()
        for measured_distance, standard_index, needle_index in sorted(candidates):
            if standard_index in used_standards or needle_index in used_needles:
                continue
            used_standards.add(standard_index)
            used_needles.add(needle_index)
            matches.append({
                "standard_index": standard_index,
                "needle_index": needle_index,
                "position_score": position_score(measured_distance),
            })

    position = (
        round(sum(match["position_score"] for match in matches) / max(len(standards), len(needles), 1))
        if position_configured
        else None
    )

    def matched_range_score(field: str, needle_field: str, required_unit: set[str]) -> int | None:
        configured = [
            (index, standard[field])
            for index, standard in enumerate(standards)
            if isinstance(standard.get(field), dict)
            and standard[field].get("unit") in required_unit
            and isinstance(standard[field].get("min"), (int, float))
            and isinstance(standard[field].get("max"), (int, float))
        ]
        if not configured:
            return None
        hits = 0
        for standard_index, value_range in configured:
            match = next((item for item in matches if item["standard_index"] == standard_index), None)
            value = needles[match["needle_index"]].get(needle_field) if match else None
            if isinstance(value, (int, float)) and value_range["min"] <= value <= value_range["max"]:
                hits += 1
        return round(hits / len(configured) * 100)

    depth = matched_range_score("needleDepth", "depthValue", {"寸", "mm"})
    retention = matched_range_score("retentionTime", "retentionMinutes", {"分钟"})
    insertion_standards = [
        (index, standard["insertionType"])
        for index, standard in enumerate(standards)
        if standard.get("insertionType")
    ]
    insertion = None
    if position_configured and insertion_standards:
        insertion_hits = 0
        for standard_index, expected_type in insertion_standards:
            match = next((item for item in matches if item["standard_index"] == standard_index), None)
            if match and match["position_score"] > 0 and needles[match["needle_index"]].get("insertionType") == expected_type:
                insertion_hits += 1
        insertion = round(insertion_hits / len(insertion_standards) * 100)

    parts = [value for value in (position, insertion, depth, retention) if value is not None]
    if not parts:
        return {"available": False, "total": None, "position": None, "insertion": None, "depth": None, "retention": None}
    total = round(sum(parts) / len(parts))
    scripts = case_data.get("feedbackScripts") or {}
    feedback_key = "excellent" if total >= 85 else "pass" if total >= 60 else "needsImprovement"
    feedback = (scripts.get(feedback_key) or {}).get("message")
    return {
        "available": True,
        "total": total,
        "position": position,
        "insertion": insertion,
        "depth": depth,
        "retention": retention,
        "feedback": feedback,
    }


# ── 路由 ───────────────────────────────────────────────

@router.post("")
async def simulated_patient_entry(request: Request, body: SPRequest):
    """模拟病患统一入口。

    支持 action: start / dialogue / help / submit / stats / mistakes /
    collections / dialog_history / history_detail / history / reset / clear
    """
    engine = _current_engine()
    current_user = getattr(request.state, "current_user", None)
    user_id = str(getattr(current_user, "user_id", "") or body.user_id).strip()
    if not user_id:
        return {"success": False, "error": "请先登录后继续"}
    sp_request = SimulatedPatientRequest(
        user_id=user_id,
        session_id=body.session_id,
        action=body.action,  # type: ignore[arg-type]
        user_input=body.user_input,
        case_id=body.case_id,
        practice_scope=body.practice_scope,
        diagnosis=body.diagnosis,
        help_type=body.help_type,
        history_id=body.history_id,
        limit=body.limit,
    )
    result: SimulatedPatientResponse = await asyncio.to_thread(engine.execute, sp_request)
    return {
        "session_id": result.session_id,
        "action": result.action,
        "success": result.success,
        "data": result.data,
        "error": result.error,
        "is_complete": result.is_complete,
        "turn_count": result.turn_count,
        "help_available": result.help_available,
    }


@router.get("/acupuncture-cases")
async def get_acupuncture_cases(request: Request):
    return {
        "success": True,
        "data": {"cases": _load_acupuncture_cases()},
    }


@router.post("/acupuncture-score")
async def score_acupuncture_case(request: Request, body: AcupunctureScoreRequest):
    current_user = getattr(request.state, "current_user", None)
    if not str(getattr(current_user, "user_id", "") or "").strip():
        return {"success": False, "error": "请先登录后继续", "data": None}
    case_data = next((item for item in _load_acupuncture_cases() if item.get("caseId") == body.case_id), None)
    if case_data is None:
        return {"success": False, "error": "病例不存在", "data": None}
    return {"success": True, "data": _score_acupuncture_case(case_data, body.needles)}


@router.get("/stats/{user_id}")
async def get_user_stats(user_id: str, request: Request):
    engine = _current_engine()
    current_user = getattr(request.state, "current_user", None)
    authenticated_user_id = str(getattr(current_user, "user_id", "") or user_id).strip()
    sp_req = SimulatedPatientRequest(user_id=authenticated_user_id, session_id="", action="stats")
    result: SimulatedPatientResponse = await asyncio.to_thread(engine.execute, sp_req)
    return {"success": result.success, "data": result.data, "error": result.error}


@router.get("/mistakes/{user_id}")
async def get_user_mistakes(user_id: str, request: Request):
    engine = _current_engine()
    current_user = getattr(request.state, "current_user", None)
    authenticated_user_id = str(getattr(current_user, "user_id", "") or user_id).strip()
    sp_req = SimulatedPatientRequest(user_id=authenticated_user_id, session_id="", action="mistakes")
    result: SimulatedPatientResponse = await asyncio.to_thread(engine.execute, sp_req)
    return {"success": result.success, "data": result.data, "error": result.error}
