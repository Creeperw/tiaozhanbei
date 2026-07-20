from APP.backend.time_utils import utc_now
import os
import json
import re
import time
import base64
import mimetypes
import threading
import urllib.parse
import urllib.request
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse, FileResponse
from sqlalchemy.orm import Session
from sqlalchemy import text
import uuid
from pydantic import BaseModel
from ..auth import get_current_user
from ..database import get_db, SessionLocal, UserModel, DbSession, DbMessage, FeedbackRecord, AgentEvent, LearningAgentContext
from ..core_learning_service import record_agent_context
from ..config import SESSION_TITLE_MAX_TOKENS, SESSION_TITLE_TEMPERATURE
from ..schemas import CreateSessionRequest, UpdateSessionRequest, Message
from ..store import FILES
from ..file_utils import get_file_content
from ..health_workflow import stream_health_workflow_events, stream_llm_generator, stream_regenerate, compress_session_context, feedback_reviewer, memory_updater, manager_client
from ..health_utils import safe_json_dumps, split_think, extract_json_object
from ..vision_parse_service import parse_visual_file

router = APIRouter()

def _thumbnail_from_video_url(url: str) -> str:
    if not _is_external_url(url):
        return ""
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower().replace("www.", "")
    if host == "youtube.com":
        query = urllib.parse.parse_qs(parsed.query)
        video_id = (query.get("v") or [""])[0]
        if video_id:
            return f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"
    if host == "youtu.be":
        video_id = parsed.path.strip("/").split("/")[0]
        if video_id:
            return f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"
    if host == "bilibili.com":
        match = re.search(r"/video/(BV[a-zA-Z0-9]+)", parsed.path)
        if not match:
            return ""
        api_url = f"https://api.bilibili.com/x/web-interface/view?bvid={match.group(1)}"
        try:
            req = urllib.request.Request(api_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="ignore"))
            pic = ((data or {}).get("data") or {}).get("pic") or ""
            return pic.replace("http://", "https://")
        except Exception:
            return ""
    return ""

@router.get("/video/thumbnail")
def get_video_thumbnail(url: str):
    return {"thumbnail": _thumbnail_from_video_url(url)}

class RegenerateMessageRequest(BaseModel):
    tools_enabled: bool | None = None
    web_search: bool | None = False
    rag_search: bool | None = False

class SwitchBranchRequest(BaseModel):
    message_id: int

def _feedback_status_from_record(record: FeedbackRecord | None) -> str | None:
    if not record:
        return None
    value = (record.rating or record.feedback_type or "").lower()
    if value in ("like", "excellent", "user_like"):
        return "like"
    if value in ("dislike", "problem", "user_dislike", "compliance_fail"):
        return "dislike"
    return None

def _is_default_session_title(title: str | None) -> bool:
    return not (title or "").strip() or (title or "").strip() == "新对话"

def _session_messages(db: Session, session_id: str) -> list[DbMessage]:
    return db.query(DbMessage).filter(DbMessage.session_id == session_id).order_by(DbMessage.id.asc()).all()

def _message_path(db: Session, session: DbSession) -> list[DbMessage]:
    messages = _session_messages(db, session.id)
    if not messages:
        return []
    by_id = {m.id: m for m in messages}
    leaf_id = session.active_leaf_message_id if session.active_leaf_message_id in by_id else messages[-1].id
    path = []
    seen = set()
    current = by_id.get(leaf_id)
    while current and current.id not in seen:
        path.append(current)
        seen.add(current.id)
        current = by_id.get(current.parent_id)
    return list(reversed(path))

def _deepest_leaf_from(db: Session, session_id: str, message_id: int) -> int:
    children_by_parent: dict[int | None, list[DbMessage]] = {}
    for message in _session_messages(db, session_id):
        children_by_parent.setdefault(message.parent_id, []).append(message)
    current_id = message_id
    while children_by_parent.get(current_id):
        current_id = max(children_by_parent[current_id], key=lambda item: item.id).id
    return current_id

def _assistant_branch_info(db: Session, message: DbMessage) -> dict | None:
    if message.role != "assistant" or message.parent_id is None:
        return None
    siblings = db.query(DbMessage).filter(
        DbMessage.session_id == message.session_id,
        DbMessage.parent_id == message.parent_id,
        DbMessage.role == "assistant",
    ).order_by(DbMessage.id.asc()).all()
    if len(siblings) <= 1:
        return None
    ids = [item.id for item in siblings]
    return {
        "index": ids.index(message.id) + 1,
        "count": len(ids),
        "prev_id": ids[ids.index(message.id) - 1] if ids.index(message.id) > 0 else None,
        "next_id": ids[ids.index(message.id) + 1] if ids.index(message.id) < len(ids) - 1 else None,
    }

def format_db_message(db_msg: DbMessage, feedback_lookup: dict[int, FeedbackRecord] | None = None, branch_info: dict | None = None):
    feedback_record = (feedback_lookup or {}).get(db_msg.id)
    feedback_status = _feedback_status_from_record(feedback_record)
    return {
        "id": db_msg.id,
        "role": db_msg.role,
        "content": db_msg.content,
        "files": json.loads(db_msg.files) if db_msg.files else [],
        "timestamp": db_msg.timestamp,
        "feedback_status": feedback_status,
        "feedback_id": feedback_record.id if feedback_record else None,
        "parent_id": db_msg.parent_id,
        "branch": branch_info,
    }

@router.get("/sessions")
def get_sessions(current_user: UserModel = Depends(get_current_user), db: Session = Depends(get_db)):
    sessions = db.query(DbSession).filter(DbSession.user_id == current_user.id).order_by(DbSession.created_at.desc()).all()
    return [{
        "id": s.id, 
        "title": s.title, 
        "created_at": s.created_at.timestamp() 
    } for s in sessions]

@router.post("/sessions")
def create_session(body: CreateSessionRequest, current_user: UserModel = Depends(get_current_user), db: Session = Depends(get_db)):
    session_id = str(uuid.uuid4())
    new_session = DbSession(
        id=session_id,
        user_id=current_user.id,
        title=body.title,
        title_auto_enabled=_is_default_session_title(body.title),
        created_at=utc_now()
    )
    db.add(new_session)
    db.commit()
    db.refresh(new_session)
    return {
        "id": new_session.id,
        "title": new_session.title,
        "created_at": new_session.created_at.timestamp(),
        "messages": []
    }

@router.delete("/sessions/{session_id}")
def delete_session(session_id: str, current_user: UserModel = Depends(get_current_user), db: Session = Depends(get_db)):
    session = db.query(DbSession).filter(DbSession.id == session_id, DbSession.user_id == current_user.id).first()
    if not session: raise HTTPException(status_code=404, detail="Session not found")
    db.execute(text("DELETE FROM feedback_records WHERE session_id = :sid"), {"sid": session_id})
    db.execute(text("DELETE FROM agent_context WHERE session_id = :sid"), {"sid": session_id})
    db.execute(text("DELETE FROM agent_events WHERE session_id = :sid"), {"sid": session_id})
    db.execute(text("DELETE FROM memory_summaries WHERE session_id = :sid"), {"sid": session_id})
    db.execute(text("DELETE FROM memory_candidates WHERE session_id = :sid"), {"sid": session_id})
    db.execute(text("DELETE FROM messages WHERE session_id = :sid"), {"sid": session_id})
    db.execute(text("DELETE FROM sessions WHERE id = :sid AND user_id = :uid"), {"sid": session_id, "uid": current_user.id})
    db.commit()
    return {"success": True}

@router.patch("/sessions/{session_id}")
def update_session(session_id: str, body: UpdateSessionRequest, current_user: UserModel = Depends(get_current_user), db: Session = Depends(get_db)):
    session = db.query(DbSession).filter(DbSession.id == session_id, DbSession.user_id == current_user.id).first()
    if not session: raise HTTPException(status_code=404, detail="Session not found")
    session.title = body.title
    session.title_auto_enabled = False
    db.commit()
    return {"success": True, "title": body.title}

@router.get("/sessions/{session_id}/messages")
def get_session_messages(session_id: str, current_user: UserModel = Depends(get_current_user), db: Session = Depends(get_db)):
    session = db.query(DbSession).filter(DbSession.id == session_id, DbSession.user_id == current_user.id).first()
    if not session: raise HTTPException(status_code=404, detail="Session not found")
    messages = _message_path(db, session)
    assistant_message_ids = [m.id for m in messages if m.role == "assistant"]
    feedback_lookup = {}
    if assistant_message_ids:
        feedback_rows = db.query(FeedbackRecord).filter(
            FeedbackRecord.user_id == current_user.id,
            FeedbackRecord.session_id == session_id,
            FeedbackRecord.message_id.in_(assistant_message_ids),
        ).order_by(FeedbackRecord.created_at.desc()).all()
        for row in feedback_rows:
            if row.message_id and row.message_id not in feedback_lookup:
                feedback_lookup[row.message_id] = row
    return [format_db_message(m, feedback_lookup, _assistant_branch_info(db, m)) for m in messages]

@router.post("/sessions/{session_id}/branch")
def switch_branch(session_id: str, body: SwitchBranchRequest, current_user: UserModel = Depends(get_current_user), db: Session = Depends(get_db)):
    session = db.query(DbSession).filter(DbSession.id == session_id, DbSession.user_id == current_user.id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    message = db.query(DbMessage).filter(DbMessage.id == body.message_id, DbMessage.session_id == session_id, DbMessage.role == "assistant").first()
    if not message:
        raise HTTPException(status_code=404, detail="Branch message not found")
    session.active_leaf_message_id = _deepest_leaf_from(db, session_id, message.id)
    db.commit()
    return {"success": True, "active_leaf_message_id": session.active_leaf_message_id}

# 视觉文件扩展名
IMAGE_EXTS = ['.jpg', '.jpeg', '.png', '.bmp', '.webp', '.tiff']
VIDEO_EXTS = ['.mp4', '.mkv', '.mov', '.avi', '.webm']

def _image_file_to_openai_content(abs_path: str) -> dict:
    mime = mimetypes.guess_type(abs_path)[0] or "image/jpeg"
    with open(abs_path, "rb") as file:
        encoded = base64.b64encode(file.read()).decode("utf-8")
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}}


def _infer_visual_task_hint(user_input: str, filename: str | None = None) -> str:
    text = f"{user_input or ''} {filename or ''}"
    if any(word in text for word in ("批改", "作业", "答案", "评分")):
        return "homework_grading"
    if any(word in text for word in ("试卷", "截图", "卷面")):
        return "paper_screenshot"
    if any(word in text for word in ("舌", "舌象")):
        return "tongue_teaching_image"
    if any(word in text for word in ("药材", "本草", "饮片")):
        return "herb_image"
    return "question_photo"

def _file_owner_matches(file_info: dict | None, user_id: int) -> bool:
    return bool(file_info) and file_info.get("uploader_id") == user_id


def _validate_user_files(files: list | None, user_id: int) -> None:
    for item in files or []:
        file_id = item.id if hasattr(item, "id") else item.get("id")
        if not file_id:
            continue
        if not _file_owner_matches(FILES.get(file_id), user_id):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="File access denied")


def _unsupported_video_message(filename: str | None) -> str:
    name = filename or "视频文件"
    return f"{name}：暂不支持视频内容直接解析，请上传题目截图或关键帧图片。"


@router.get("/files/image/{file_id}")
def get_image_file(file_id: str, current_user: UserModel = Depends(get_current_user)):
    """提供图片/视频文件预览流"""
    file_info = FILES.get(file_id)
    if not file_info: raise HTTPException(status_code=404, detail="File info not found")
    if not _file_owner_matches(file_info, current_user.id): raise HTTPException(status_code=403, detail="File access denied")
    file_path = file_info.get("saved_path")
    if not file_path or not os.path.exists(file_path): raise HTTPException(status_code=404, detail="File not found on disk")
    return FileResponse(file_path)

def get_file_abs_path(file_id: str) -> str:
    file_info = FILES.get(file_id)
    if file_info and file_info.get("saved_path"):
        return os.path.abspath(file_info["saved_path"])
    return ""

def _strip_planner_machine_text(raw: str) -> str:
    """Keep planner reasoning readable while removing machine JSON/tool XML/end tokens."""
    if not raw:
        return ""
    text = raw.replace("<|im_end|>", "")
    text = re.sub(r"</?think>", "", text)
    text = re.sub(r"<tool_response>[\s\S]*?</tool_response>", "", text)
    text = re.sub(r"<\|im_start\|>[\s\S]*", "", text)
    text = re.sub(r"\{\s*\"intent\"[\s\S]*?\}\s*", "", text)
    text = re.sub(r"<tool_call>[\s\S]*?</tool_call>", "", text)
    return text.strip()

def _safe_stream_payload(value):
    """Prevent custom stream tags from being prematurely closed by model/tool text."""
    if isinstance(value, dict):
        return {k: _safe_stream_payload(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_safe_stream_payload(v) for v in value]
    if isinstance(value, str):
        return value.replace("<<", "＜＜").replace(">>", "＞＞")
    return value

def _build_planning_payload(state: dict) -> tuple[list, str]:
    nodes = []
    intent = state.get("intent") or "其他"

    nodes.append({
        "type": "context",
        "title": "上下文与记忆",
        "value": "已完成",
        "detail": "已加载用户画像、个性化记忆和近期对话上下文，并交给规划智能体作为参考。",
    })

    reasoning_parts = []
    for trace in state.get("planner_trace", []):
        action = trace.get("action") or {}
        for call in trace.get("tool_calls") or []:
            nodes.append({
                "type": "tool",
                "title": "调用工具",
                "name": call.get("name", ""),
                "query": call.get("query", ""),
            })
        if action.get("action") == "planning_finish":
            nodes.append({
                "type": "finish",
                "title": "规划完成",
                "value": action.get("finish_reason", "信息足够，进入执行阶段。"),
            })
        cleaned = _strip_planner_machine_text(trace.get("raw", ""))
        if cleaned:
            reasoning_parts.append(cleaned)

    for item in state.get("tool_results", []):
        nodes.append({
            "type": "tool_result",
            "title": "工具返回",
            "name": item.get("tool", ""),
            "value": (item.get("content", "") or "")[:180],
        })

    reasoning = "\n\n".join(reasoning_parts).strip()
    nodes.insert(0, {"type": "intent", "title": "意图", "value": intent, "detail": reasoning})
    return nodes, reasoning

def _build_execution_payload(state: dict) -> list:
    nodes = [{
        "type": "execute",
        "title": "执行阶段",
        "value": "基于识别意图、回答建议、个性化内容和外部参考信息生成最终回复。",
        "detail": state.get("answer_think") or "",
    }]
    review = state.get("review") or {}
    if review:
        nodes.append({
            "type": "review",
            "title": "反馈审核",
            "value": "通过" if review.get("approved", True) else "不通过，已重新生成",
            "detail": review.get("reason", ""),
        })
    return nodes

def _is_external_url(value: str) -> bool:
    return bool(re.match(r"^https?://", value or "", flags=re.I))

def _parse_web_refs(tool_name: str, query: str, content: str) -> list[dict]:
    refs = []
    blocks = re.split(r"\n\s*\n", content or "")
    for block in blocks:
        title_match = re.search(r"^Title:\s*(.+)$", block, flags=re.M)
        url_match = re.search(r"^URL:\s*(\S+)", block, flags=re.M)
        content_match = re.search(r"^Content:\s*([\s\S]+)$", block, flags=re.M)
        url = (url_match.group(1).strip() if url_match else "")
        if not _is_external_url(url):
            continue
        title = (title_match.group(1).strip() if title_match else url)
        snippet = (content_match.group(1).strip() if content_match else block.strip())
        refs.append({
            "title": title,
            "url": url,
            "query": query,
            "snippet": snippet[:500],
            "content": snippet[:1200],
            "source": tool_name,
            "type": "web",
        })
    return refs

def _parse_video_refs(tool_name: str, query: str, content: str) -> list[dict]:
    videos = []
    blocks = re.split(r"\n\s*\n", content or "")
    for block in blocks:
        title_match = re.search(r"^Title:\s*(.+)$", block, flags=re.M)
        url_match = re.search(r"^URL:\s*(\S+)", block, flags=re.M)
        image_match = re.search(r"^Image:\s*(\S+)", block, flags=re.M)
        favicon_match = re.search(r"^Favicon:\s*(\S+)", block, flags=re.M)
        author_match = re.search(r"^Author:\s*(.+)$", block, flags=re.M)
        content_match = re.search(r"^Content:\s*([\s\S]+)$", block, flags=re.M)
        url = (url_match.group(1).strip() if url_match else "")
        if not _is_external_url(url):
            continue
        title = (title_match.group(1).strip() if title_match else url)
        snippet = (content_match.group(1).strip() if content_match else block.strip())
        videos.append({
            "title": title,
            "url": url,
            "thumbnail": image_match.group(1).strip() if image_match else "",
            "favicon": favicon_match.group(1).strip() if favicon_match else "",
            "author": author_match.group(1).strip() if author_match else "",
            "query": query,
            "snippet": snippet[:280],
            "content": snippet[:800],
            "source": tool_name,
            "type": "video",
        })
    return videos

def _build_videos_from_tool_results(tool_results: list[dict]) -> list[dict]:
    videos = []
    for item in tool_results:
        tool_name = item.get("tool") or ""
        if tool_name != "search_health_video":
            continue
        content = (item.get("content", "") or "").strip()
        if not content or "未找到相关视频" in content or "视频检索未返回" in content:
            continue
        videos.extend(_parse_video_refs(tool_name, item.get("query", "") or "", content))
    return videos

def _build_refs_from_tool_results(tool_results: list[dict]) -> list[dict]:
    refs = []
    for item in tool_results:
        tool_name = item.get("tool") or "检索来源"
        if tool_name == "search_health_video":
            continue
        query = item.get("query", "") or ""
        content = (item.get("content", "") or "").strip()
        if not content or "未检索到相关内容" in content or "未找到相关" in content:
            continue
        if tool_name == "search_rag":
            refs.append({
                "title": "本地知识库检索",
                "url": "",
                "query": query,
                "snippet": content[:500],
                "content": content,
                "source": tool_name,
                "type": "rag",
            })
            continue
        web_refs = _parse_web_refs(tool_name, query, content)
        if web_refs:
            refs.extend(web_refs)
        else:
            refs.append({
                "title": "网络检索结果",
                "url": "",
                "query": query,
                "snippet": content[:500],
                "content": content,
                "source": tool_name,
                "type": "web",
            })
    return refs

def _filter_visible_stream_delta(buffer: str, delta: str) -> tuple[str, str]:
    """Return (visible_delta, new_buffer) while suppressing streamed <think> blocks."""
    text_value = buffer + (delta or "")
    visible_parts = []
    while text_value:
        start = text_value.find("<think>")
        if start == -1:
            if text_value.endswith("<") or text_value.endswith("<think") or text_value.endswith("<think>"):
                return "".join(visible_parts), text_value
            visible_parts.append(text_value)
            return "".join(visible_parts), ""
        visible_parts.append(text_value[:start])
        end = text_value.find("</think>", start + len("<think>"))
        if end == -1:
            return "".join(visible_parts), text_value[start:]
        text_value = text_value[end + len("</think>"):]
    return "".join(visible_parts), ""

def _longest_suffix_prefix(text_value: str, marker: str) -> int:
    max_len = min(len(text_value), len(marker) - 1)
    for size in range(max_len, 0, -1):
        if marker.startswith(text_value[-size:]):
            return size
    return 0

def _split_visible_and_think_stream_delta(state: dict, delta: str) -> tuple[str, str]:
    """Split streamed executor deltas into visible answer text and hidden thinking text.

    The visible part is sent to the main answer. The thinking part is emitted as
    execution trace events so it appears in the right sidebar only.
    """
    text_value = str(state.get("pending", "")) + (delta or "")
    state["pending"] = ""
    visible_parts: list[str] = []
    think_parts: list[str] = []
    idx = 0

    while idx < len(text_value):
        if state.get("in_think", False):
            end = text_value.find("</think>", idx)
            if end == -1:
                hold = _longest_suffix_prefix(text_value[idx:], "</think>")
                emit_end = len(text_value) - hold
                if emit_end > idx:
                    think_parts.append(text_value[idx:emit_end])
                if hold:
                    state["pending"] = text_value[emit_end:]
                break
            think_parts.append(text_value[idx:end])
            idx = end + len("</think>")
            state["in_think"] = False
            continue

        start = text_value.find("<think>", idx)
        if start == -1:
            hold = _longest_suffix_prefix(text_value[idx:], "<think>")
            emit_end = len(text_value) - hold
            if emit_end > idx:
                visible_parts.append(text_value[idx:emit_end])
            if hold:
                state["pending"] = text_value[emit_end:]
            break
        if start > idx:
            visible_parts.append(text_value[idx:start])
        idx = start + len("<think>")
        state["in_think"] = True

    return "".join(visible_parts), "".join(think_parts)

def _clean_generated_title(raw: str, fallback: str = "学习咨询") -> str:
    _, visible = split_think(raw or "")
    text_value = (visible or raw or "").strip()
    parsed = extract_json_object(text_value)
    if isinstance(parsed, dict) and parsed.get("title"):
        text_value = str(parsed.get("title") or "")
    text_value = re.sub(r"[`*_#>\[\]{}]", "", text_value)
    text_value = text_value.strip().strip('"\'“”‘’：:，,。.!！?？')
    text_value = re.sub(r"\s+", "", text_value)
    return (text_value or fallback)[:24]

def _generate_session_title_from_context(context: str, fallback: str = "学习咨询") -> str:
    prompt = (
        "/no_think\n"
        "请只根据用户本轮问题生成一个简短中文会话标题。\n"
        "要求：只输出标题本身；不超过12个汉字；不要引号、标点、解释；突出核心学习主题。\n\n"
        f"用户问题：\n{context}"
    )
    raw = manager_client.chat([
        {"role": "system", "content": "你是信息管理智能体，负责为培训助手对话生成简洁标题。只输出标题。"},
        {"role": "user", "content": prompt},
    ], temperature=SESSION_TITLE_TEMPERATURE, max_tokens=SESSION_TITLE_MAX_TOKENS)
    return _clean_generated_title(raw, fallback=fallback)

def _write_model_session_title(db: Session, session: DbSession, user_id: int, context: str, turn_count: int, reason: str) -> None:
    title = _generate_session_title_from_context(context, fallback=session.title or "学习咨询")
    session.title = title
    db.add(AgentEvent(
        user_id=user_id,
        session_id=session.id,
        agent_name="title_agent",
        event_type=reason,
        output_summary=title,
        payload=safe_json_dumps({"user_turn_count": turn_count}),
    ))
    db.commit()

def _ensure_initial_session_title(db: Session, session: DbSession, user_id: int, user_input: str, current_files: list | None = None) -> None:
    """首轮回答完成后生成一次模型标题；标题只基于用户问题，不参考助手回答。"""
    if not session or not getattr(session, "title_auto_enabled", True) or not _is_default_session_title(session.title):
        return
    has_title_event = db.query(AgentEvent).filter(
        AgentEvent.session_id == session.id,
        AgentEvent.agent_name == "title_agent",
    ).first() is not None
    if has_title_event:
        return
    user_turn_count = db.query(DbMessage).filter(DbMessage.session_id == session.id, DbMessage.role == "user").count()
    if user_turn_count > 1:
        return
    file_names = []
    for item in current_files or []:
        file_names.append(item.name if hasattr(item, "name") else item.get("name", ""))
    context = user_input or "用户上传了附件"
    if file_names:
        context += "\n附件：" + "、".join(name for name in file_names if name)
    try:
        _write_model_session_title(db, session, user_id, context, 0, "initial_title")
    except Exception:
        # 模型标题失败时保持默认标题，不使用临时标题伪装成模型生成结果。
        db.rollback()

def vl_chat_generator(
    user_input: str,
    session_id: str,
    current_files: list,
    tools_enabled: bool,
    db: Session,
    replace_message_id: int | None = None,
    context_before_message_id: int | None = None,
    parent_message_id: int | None = None,
    assistant_parent_id: int | None = None,
):
    """
    健康管理 LangGraph 多智能体生成器。
    """
    
    # --- 1. 构建当前 User 消息 ---
    user_content_list = []
    visual_content_list = []
    visual_file_names = []
    visual_parse_summaries = []
    file_text_context = "" # 用于存储文档类文件的内容
    
    # A. 处理所有附件
    if current_files:
        for f in current_files:
            fid = f.id if hasattr(f, 'id') else f.get('id')
            fname = f.name if hasattr(f, 'name') else f.get('name')
            ext = os.path.splitext(fname)[1].lower()
            
            abs_path = get_file_abs_path(fid)
            
            if ext in IMAGE_EXTS and abs_path:
                visual_item = {"type": "image", "image": f"file://{abs_path}"}
                user_content_list.append(visual_item)
                visual_content_list.append(_image_file_to_openai_content(abs_path))
                visual_file_names.append(fname)
                try:
                    parsed = parse_visual_file(
                        abs_path,
                        task_hint=_infer_visual_task_hint(user_input, fname),
                        db=db,
                        user_id=getattr(db, "_current_user_id", 0),
                        session_id=session_id,
                    )
                    visual_parse_summaries.append({"filename": fname, "result": parsed.model_dump()})
                except ValueError:
                    pass
                except Exception:
                    visual_parse_summaries.append({"filename": fname, "error": "vision_parse_failed"})
            elif ext in VIDEO_EXTS and abs_path:
                visual_item = {"type": "video", "video": f"file://{abs_path}"}
                user_content_list.append(visual_item)
                file_text_context += f"\n\n--- Video: {fname} ---\n{_unsupported_video_message(fname)}\n"
            else:
                # 处理非视觉文件 (docx, pdf, txt, code)
                # 使用 get_file_content 读取文本内容
                content = get_file_content(fid)
                file_text_context += f"\n\n--- Document: {fname} ---\n{content}\n"
    
    # B. 拼接文本 Prompt (用户输入 + 文档内容)
    final_text_input = user_input or ""
    if file_text_context:
        final_text_input += f"\n\n【已上传的文档内容】:{file_text_context}"
    if visual_parse_summaries:
        final_text_input += "\n\n【视觉模型结构化解析】:\n" + safe_json_dumps(_safe_stream_payload(visual_parse_summaries))

    if final_text_input:
        user_content_list.append({"type": "text", "text": final_text_input})
    
    if not user_content_list:
        yield "Error: No input content provided.".encode("utf-8")
        return

    # --- 2. 逐阶段调用健康工作流并输出事件流 ---
    record_agent_context(
        db,
        user_id=getattr(db, "_current_user_id", 0),
        session_id=session_id,
        source_agent="chat_route",
        target_agent="health_workflow",
        purpose="generate_reply",
        user_input=user_input,
        tools_enabled=tools_enabled,
        files=current_files,
    )
    db.commit()
    yield "<<STATUS:analyzing:正在加载个性化记忆与历史上下文...>>".encode("utf-8")
    process_chunks = ["<think>\n"]
    yield process_chunks[0].encode("utf-8")

    state = {}
    for event, next_state in stream_health_workflow_events(
        db=db,
        user_id=getattr(db, "_current_user_id", 0),
        session_id=session_id,
        user_question=final_text_input or "请结合上传的多模态内容进行健康管理分析。",
        file_context=file_text_context,
        enable_search=tools_enabled,
        enable_rag=tools_enabled,
        tools_enabled=tools_enabled,
        visual_content=visual_content_list,
        visual_file_names=visual_file_names,
        context_before_message_id=context_before_message_id,
        stop_before_execution=True,
    ):
        state = next_state
        event.setdefault("ts", int(time.time() * 1000))
        event_chunk = f"<<EV:{safe_json_dumps(_safe_stream_payload(event))}>>"
        process_chunks.append(event_chunk)
        yield event_chunk.encode("utf-8")
        time.sleep(0.02)

    process_chunks.append("\n</think>\n")
    yield process_chunks[-1].encode("utf-8")

    answer_parts = []
    executor_split_state = {"in_think": False, "pending": ""}
    executor_think_buffer = ""

    def _build_result_metadata_block() -> str:
        refs = _build_refs_from_tool_results(state.get("tool_results", []))
        videos = _build_videos_from_tool_results(state.get("tool_results", []))
        metadata_chunks = []
        if refs:
            metadata_chunks.append(f"<<REFS:{safe_json_dumps(_safe_stream_payload(refs))}>>")
        if videos:
            metadata_chunks.append(f"<<VIDEOS:{safe_json_dumps(_safe_stream_payload(videos))}>>")
        return ("\n" + "\n".join(metadata_chunks)) if metadata_chunks else ""

    def _emit_executor_think_delta(text_value: str):
        cleaned = (text_value or "").strip()
        if not cleaned:
            return None
        event = {
            "type": "execution_delta",
            "title": "执行阶段",
            "text": cleaned,
            "ts": int(time.time() * 1000),
        }
        chunk = f"<<EV:{safe_json_dumps(_safe_stream_payload(event))}>>"
        process_chunks.append(chunk)
        return chunk

    try:
        for delta in stream_llm_generator(state):
            visible_delta, think_delta = _split_visible_and_think_stream_delta(executor_split_state, delta)
            if think_delta:
                executor_think_buffer += think_delta
                if "\n" in executor_think_buffer or len(executor_think_buffer) >= 80:
                    think_chunk = _emit_executor_think_delta(executor_think_buffer)
                    executor_think_buffer = ""
                    if think_chunk:
                        yield think_chunk.encode("utf-8")
            if visible_delta:
                answer_parts.append(visible_delta)
                yield visible_delta.encode("utf-8")
        if executor_split_state.get("pending"):
            if executor_split_state.get("in_think"):
                executor_think_buffer += executor_split_state.get("pending", "")
            else:
                pending_visible = executor_split_state.get("pending", "")
                answer_parts.append(pending_visible)
                yield pending_visible.encode("utf-8")
            executor_split_state["pending"] = ""
        if executor_think_buffer.strip():
            think_chunk = _emit_executor_think_delta(executor_think_buffer)
            executor_think_buffer = ""
            if think_chunk:
                yield think_chunk.encode("utf-8")
    except Exception:
        fallback_answer = "抱歉，本轮生成结果未能形成可展示的正式回答，请再试一次。"
        state["answer_visible"] = fallback_answer
        answer_parts = [fallback_answer]
        yield fallback_answer.encode("utf-8")

    execution_done_event = {
        "type": "execution_done",
        "title": "执行阶段",
        "text": "正式回答输出完成。",
        "ts": int(time.time() * 1000),
    }
    execution_done_chunk = f"<<EV:{safe_json_dumps(_safe_stream_payload(execution_done_event))}>>"
    process_chunks.append(execution_done_chunk)
    yield execution_done_chunk.encode("utf-8")

    metadata_block = _build_result_metadata_block()
    if metadata_block:
        yield metadata_block.encode("utf-8")

    feedback_start_event = {
        "type": "feedback_start",
        "title": "反馈审核",
        "text": "反馈智能体正在检查回答质量与合规性。",
        "ts": int(time.time() * 1000),
    }
    feedback_start_chunk = f"<<EV:{safe_json_dumps(_safe_stream_payload(feedback_start_event))}>>"
    process_chunks.append(feedback_start_chunk)
    yield feedback_start_chunk.encode("utf-8")

    state = feedback_reviewer(state)
    if not state.get("review", {}).get("approved", True):
        feedback_regenerate_event = {
            "type": "feedback_regenerate",
            "title": "反馈审核",
            "text": state.get("review", {}).get("reason", "审核未通过，正在撤回并重新生成。"),
            "ts": int(time.time() * 1000),
        }
        feedback_regenerate_chunk = f"<<EV:{safe_json_dumps(_safe_stream_payload(feedback_regenerate_event))}>>"
        process_chunks.append(feedback_regenerate_chunk)
        yield feedback_regenerate_chunk.encode("utf-8")

        rollback_chunk = f"<<ROLLBACK:{safe_json_dumps(_safe_stream_payload({'reason': feedback_regenerate_event['text']}))}>>"
        yield rollback_chunk.encode("utf-8")

        answer_parts = []
        executor_split_state = {"in_think": False, "pending": ""}
        executor_think_buffer = ""
        for delta in stream_regenerate(state):
            visible_delta, think_delta = _split_visible_and_think_stream_delta(executor_split_state, delta)
            if think_delta:
                executor_think_buffer += think_delta
                if "\n" in executor_think_buffer or len(executor_think_buffer) >= 80:
                    think_chunk = _emit_executor_think_delta(executor_think_buffer)
                    executor_think_buffer = ""
                    if think_chunk:
                        yield think_chunk.encode("utf-8")
            if visible_delta:
                answer_parts.append(visible_delta)
                yield visible_delta.encode("utf-8")
        if executor_split_state.get("pending"):
            if executor_split_state.get("in_think"):
                executor_think_buffer += executor_split_state.get("pending", "")
            else:
                pending_visible = executor_split_state.get("pending", "")
                answer_parts.append(pending_visible)
                yield pending_visible.encode("utf-8")
            executor_split_state["pending"] = ""
        if executor_think_buffer.strip():
            think_chunk = _emit_executor_think_delta(executor_think_buffer)
            executor_think_buffer = ""
            if think_chunk:
                yield think_chunk.encode("utf-8")

        regenerated_done_event = {
            "type": "execution_done",
            "title": "执行阶段",
            "text": "已根据反馈重新生成正式回答。",
            "ts": int(time.time() * 1000),
        }
        regenerated_done_chunk = f"<<EV:{safe_json_dumps(_safe_stream_payload(regenerated_done_event))}>>"
        process_chunks.append(regenerated_done_chunk)
        yield regenerated_done_chunk.encode("utf-8")

        metadata_block = _build_result_metadata_block()
        if metadata_block:
            yield metadata_block.encode("utf-8")
        state = feedback_reviewer(state)

    feedback_done_event = {
        "type": "feedback_done",
        "title": "反馈审核",
        "text": state.get("review", {}).get("reason", "审核通过。"),
        "approved": state.get("review", {}).get("approved", True),
        "ts": int(time.time() * 1000),
    }
    feedback_done_chunk = f"<<EV:{safe_json_dumps(_safe_stream_payload(feedback_done_event))}>>"
    process_chunks.append(feedback_done_chunk)
    yield feedback_done_chunk.encode("utf-8")
    state = memory_updater(state)
    process_content = "".join(process_chunks)

    answer_visible = state.get("answer_visible") or "".join(answer_parts)
    full_response = answer_visible + metadata_block

    # --- 4. 存储 ---
    # 去除状态标签后存储
    clean_content = re.sub(r'<<STATUS:.*?>>', '', process_content + full_response)
    
    files_data = [f.model_dump() if hasattr(f, 'model_dump') else f for f in current_files] if current_files else []
    files_json = json.dumps(files_data)
    
    if replace_message_id:
        ai_msg_db = db.query(DbMessage).filter(DbMessage.id == replace_message_id, DbMessage.session_id == session_id, DbMessage.role == "assistant").first()
        if ai_msg_db:
            ai_msg_db.content = clean_content
            ai_msg_db.timestamp = datetime.now().strftime("%H:%M")
            db.execute(text("DELETE FROM feedback_records WHERE message_id = :mid"), {"mid": replace_message_id})
    elif assistant_parent_id:
        ai_msg_db = DbMessage(session_id=session_id, parent_id=assistant_parent_id, role="assistant", content=clean_content, files="[]", timestamp=datetime.now().strftime("%H:%M"))
        db.add(ai_msg_db)
        db.flush()
        session = db.query(DbSession).filter(DbSession.id == session_id).first()
        if session:
            session.active_leaf_message_id = ai_msg_db.id
    else:
        user_msg_db = DbMessage(session_id=session_id, parent_id=parent_message_id, role="user", content=user_input, files=files_json, timestamp=datetime.now().strftime("%H:%M"))
        db.add(user_msg_db)
        db.flush()
        
        ai_msg_db = DbMessage(session_id=session_id, parent_id=user_msg_db.id, role="assistant", content=clean_content, files="[]", timestamp=datetime.now().strftime("%H:%M"))
        db.add(ai_msg_db)
        db.flush()
        session = db.query(DbSession).filter(DbSession.id == session_id).first()
        if session:
            session.active_leaf_message_id = ai_msg_db.id
    
    db.commit()

    user_id_for_compression = getattr(db, "_current_user_id", 0)
    intent_for_compression = state.get("intent") or "unknown"
    should_ensure_title = not replace_message_id

    if should_ensure_title:
        try:
            title_session = db.query(DbSession).filter(
                DbSession.id == session_id,
                DbSession.user_id == user_id_for_compression,
            ).first()
            _ensure_initial_session_title(db, title_session, user_id_for_compression, user_input, current_files)
        except Exception:
            db.rollback()

    def _post_turn_maintenance():
        bg_db = SessionLocal()
        try:
            compress_session_context(
                bg_db,
                user_id_for_compression,
                session_id,
                reason="task_completed",
                intent=intent_for_compression,
            )
        finally:
            bg_db.close()

    threading.Thread(target=_post_turn_maintenance, daemon=True).start()

# --- 路由入口 ---
@router.post("/chat/{session_id}")
async def chat(session_id: str, body: Message, current_user: UserModel = Depends(get_current_user), db: Session = Depends(get_db)):
    """
    [VL Mode] 视觉对话接口，接管 /chat/{session_id}
    """
    session = db.query(DbSession).filter(DbSession.id == session_id, DbSession.user_id == current_user.id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    # 获取前端的检索开关
    tools_enabled = getattr(body, 'tools_enabled', None)
    if tools_enabled is None:
        tools_enabled = bool(getattr(body, 'web_search', False) or getattr(body, 'rag_search', False))
    _validate_user_files(body.files, current_user.id)
    setattr(db, "_current_user_id", current_user.id)

    return StreamingResponse(
        vl_chat_generator(body.content, session_id, body.files, bool(tools_enabled), db, parent_message_id=session.active_leaf_message_id), 
        media_type="text/plain"
    )

@router.post("/chat/{session_id}/messages/{message_id}/regenerate")
async def regenerate_message(session_id: str, message_id: int, body: RegenerateMessageRequest, current_user: UserModel = Depends(get_current_user), db: Session = Depends(get_db)):
    """从指定 assistant 回复处创建一个新的对话分支。"""
    session = db.query(DbSession).filter(DbSession.id == session_id, DbSession.user_id == current_user.id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    target = db.query(DbMessage).filter(DbMessage.id == message_id, DbMessage.session_id == session_id, DbMessage.role == "assistant").first()
    if not target:
        raise HTTPException(status_code=404, detail="Assistant message not found")
    source_user = db.query(DbMessage).filter(
        DbMessage.id == target.parent_id,
        DbMessage.session_id == session_id,
        DbMessage.role == "user",
    ).first()
    if not source_user:
        raise HTTPException(status_code=400, detail="No source user message found")

    tools_enabled = body.tools_enabled
    if tools_enabled is None:
        tools_enabled = bool(body.web_search or body.rag_search)
    setattr(db, "_current_user_id", current_user.id)
    current_files = json.loads(source_user.files) if source_user.files else []
    _validate_user_files(current_files, current_user.id)
    return StreamingResponse(
        vl_chat_generator(
            source_user.content or "",
            session_id,
            current_files,
            bool(tools_enabled),
            db,
            context_before_message_id=source_user.id,
            assistant_parent_id=source_user.id,
        ),
        media_type="text/plain",
    )