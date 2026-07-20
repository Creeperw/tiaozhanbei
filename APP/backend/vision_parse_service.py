from __future__ import annotations

import base64
import json
import mimetypes
import os
import urllib.request
from typing import Any, Callable

from pydantic import BaseModel, Field

from APP.backend.cross_validation_service import validate_visual_parse_result as cross_validate_output


class VisualParseResult(BaseModel):
    image_type: str
    question: str = ""
    student_answer: str = ""
    visual_observations: list[str] = Field(default_factory=list)
    uncertain_parts: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    raw_model_metadata: dict[str, Any] = Field(default_factory=dict)


def _vision_api_base_url() -> str:
    return os.getenv("VISION_API_BASE_URL", "").rstrip("/")


def _vision_api_model() -> str:
    return os.getenv("VISION_API_MODEL", "qwen3-vl-flash")


def _vision_api_key() -> str:
    return os.getenv("VISION_API_KEY", "")


def _vision_api_timeout_seconds() -> int:
    raw = os.getenv("VISION_API_TIMEOUT_SECONDS", "30")
    try:
        return int(raw)
    except ValueError:
        return 30


def _default_http_post(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _prompt_for_task(task_hint: str) -> str:
    base = (
        "你是时珍智训的视觉解析助手。请只做教学场景的图片信息抽取，"
        "输出 JSON，字段包含 image_type、question、student_answer、visual_observations、uncertain_parts、confidence。"
        "confidence 必须是 0 到 1 之间的小数。"
    )
    if task_hint in {"tongue_teaching_image", "herb_image"}:
        return base + " 舌象或药材图片只能用于教学辨识，不能输出真实诊断、处方或治疗结论；不确定处写入 uncertain_parts。"
    return base + " 对拍题、作业批改或试卷截图，请尽量抽取题干、学生答案和可见依据；不确定处写入 uncertain_parts。"


def _extract_model_json(response: dict[str, Any]) -> dict[str, Any]:
    choices = response.get("choices") or []
    if not choices:
        return {}
    content = ((choices[0].get("message") or {}).get("content") or "").strip()
    if not content:
        return {}
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        start = content.find("{")
        end = content.rfind("}")
        if start >= 0 and end > start:
            return json.loads(content[start : end + 1])
        return {}


def _anchor_payload(data: dict[str, Any], key: str) -> list[Any]:
    value = data.get(key)
    return value if isinstance(value, list) else []


def _normalise_result(data: dict[str, Any], task_hint: str, response: dict[str, Any]) -> VisualParseResult:
    confidence = data.get("confidence", 0.0)
    try:
        confidence_value = float(confidence)
    except (TypeError, ValueError):
        confidence_value = 0.0
    confidence_value = max(0.0, min(1.0, confidence_value))
    uncertain_parts = [str(item) for item in data.get("uncertain_parts") or []]
    if task_hint in {"tongue_teaching_image", "herb_image"} and "不能据此做真实诊断" not in uncertain_parts:
        uncertain_parts.append("不能据此做真实诊断")
    return VisualParseResult(
        image_type=str(data.get("image_type") or task_hint),
        question=str(data.get("question") or ""),
        student_answer=str(data.get("student_answer") or ""),
        visual_observations=[str(item) for item in data.get("visual_observations") or []],
        uncertain_parts=uncertain_parts,
        confidence=confidence_value,
        raw_model_metadata={
            "id": response.get("id"),
            "model": response.get("model"),
            "evidence_spans": _anchor_payload(data, "evidence_spans"),
            "ocr_spans": _anchor_payload(data, "ocr_spans"),
            "source_boxes": _anchor_payload(data, "source_boxes"),
        },
    )


def parse_visual_task(
    *,
    image_base64: str,
    task_hint: str,
    mime_type: str = "image/jpeg",
    http_post: Callable[..., dict[str, Any]] = _default_http_post,
    api_base_url: str | None = None,
    api_key: str | None = None,
    db: Any | None = None,
    user_id: int | None = None,
    session_id: str | None = None,
) -> VisualParseResult:
    base_url = (api_base_url or _vision_api_base_url()).rstrip("/")
    api_key_value = api_key or _vision_api_key()
    if not base_url:
        raise ValueError("VISION_API_BASE_URL is required")
    if not api_key_value:
        raise ValueError("VISION_API_KEY is required")

    payload = {
        "model": _vision_api_model(),
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _prompt_for_task(task_hint)},
                    {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{image_base64}"}},
                ],
            }
        ],
        "temperature": 0.1,
    }
    response = http_post(
        url=f"{base_url}/chat/completions",
        payload=payload,
        headers={"Authorization": f"Bearer {api_key_value}", "Content-Type": "application/json"},
        timeout=_vision_api_timeout_seconds(),
    )
    result = _normalise_result(_extract_model_json(response), task_hint, response)
    review, summary = cross_validate_output(
        result=result,
        task_hint=task_hint,
        db=db,
        user_id=user_id,
        session_id=session_id,
    )
    result.raw_model_metadata = {
        **result.raw_model_metadata,
        "review_decision": review.model_dump(),
        "review_summary": summary,
    }
    return result


def parse_visual_file(
    file_path: str,
    *,
    task_hint: str,
    http_post: Callable[..., dict[str, Any]] = _default_http_post,
    db: Any | None = None,
    user_id: int | None = None,
    session_id: str | None = None,
) -> VisualParseResult:
    with open(file_path, "rb") as handle:
        image_base64 = base64.b64encode(handle.read()).decode("ascii")
    mime_type = mimetypes.guess_type(file_path)[0] or "image/jpeg"
    return parse_visual_task(
        image_base64=image_base64,
        task_hint=task_hint,
        mime_type=mime_type,
        http_post=http_post,
        db=db,
        user_id=user_id,
        session_id=session_id,
    )
