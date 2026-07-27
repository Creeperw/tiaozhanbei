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
        payload = json.loads(ACUPUNCTURE_CASE_DATA_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return payload if isinstance(payload, list) else []


# ── 请求模型 ──────────────────────────────────────────

class SPRequest(BaseModel):
    user_id: str = Field(default="", max_length=128)
    session_id: str = Field(..., min_length=1, max_length=128)
    action: str = Field(
        ...,
        pattern=r"^(start|dialogue|help|submit|stats|mistakes|collections|history|clear|reset|dialog_history|history_detail)$",
    )
    user_input: str = Field(default="", max_length=2000)
    case_id: Optional[str] = Field(default=None, max_length=128)
    practice_scope: str = Field(default="full")
    diagnosis: Optional[dict] = Field(default=None)
    help_type: Optional[str] = Field(default=None, pattern=r"^(question|interpretation)$")
    history_id: Optional[str] = Field(default=None, max_length=128)
    limit: int = Field(default=100, ge=1, le=500)


def _current_engine() -> SimulatedPatientEngine:
    if _engine is None:
        raise RuntimeError("SimulatedPatientEngine not initialized — call init_engine() first")
    return _engine


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
    current_user = getattr(request.state, "current_user", None)
    user_id = str(getattr(current_user, "user_id", "") or "").strip()
    if not user_id:
        return {"success": False, "error": "请先登录后继续", "data": {"cases": []}}
    return {
        "success": True,
        "data": {"cases": _load_acupuncture_cases()},
    }


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
