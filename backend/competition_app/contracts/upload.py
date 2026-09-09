"""Read-only progress projection over each upload's existing source of truth."""
from typing import Literal

from pydantic import BaseModel


class UploadProgress(BaseModel):
    task_id: str
    kind: Literal["textbook", "syllabus", "personal_questions", "admin_questions"]
    state: Literal["queued", "running", "needs_review", "succeeded", "failed", "unknown"]
    source_status: str
    stage: str
    label: str


_STATES = {
    "textbook": {"running": "running", "done": "succeeded", "failed": "failed"},
    "syllabus": {"processing": "running", "success": "succeeded", "failed": "failed"},
    "personal_questions": {"processing": "running", "preview_ready": "needs_review",
                           "needs_human_review": "needs_review", "failed": "failed"},
    "admin_questions": {"queued": "queued", "running": "running",
                        "completed": "succeeded", "failed": "failed"},
}
_LABELS = {"queued": "等待处理", "running": "正在处理", "needs_review": "等待确认或审核",
           "succeeded": "处理完成", "failed": "处理失败", "unknown": "状态待核实"}


def upload_progress(kind: str, task_id: str, source_status: str, *,
                    stage: str = "", label: str = "") -> dict:
    state = _STATES[kind].get(source_status, "unknown")
    return UploadProgress(task_id=task_id, kind=kind, state=state,
        source_status=source_status, stage=stage or state,
        label=label or _LABELS[state]).model_dump()