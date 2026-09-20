import functools
import inspect
import json
import logging
import re
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

import httpx

from APP.backend.config import LLM_TIMEOUT_SECONDS
from competition_app.llm.upload_provider import new_upload_session, upload_provider_headers

logger = logging.getLogger(__name__)


def _log_token_budget_truncation(stop_reason: Any, max_tokens: int) -> None:
    """上游因预算用尽而截断时留痕。

    截断的响应体仍是 HTTP 200，但 JSON 会被截成半截，调用方只能拿到空结果，
    最终静默降级（模板解析、规则批改）。这里必须留下可检索的证据。
    """
    if str(stop_reason or "") in {"max_tokens", "length"}:
        logger.warning(
            "model output truncated by token budget: stop_reason=%s max_tokens=%s",
            stop_reason,
            max_tokens,
        )


# 上游返回这些状态码时按“模型服务不可用”处理：限流、网关过载、上游故障。
_UNAVAILABLE_STATUS_CODES = frozenset(
    {408, 425, 429, 500, 502, 503, 504, 521, 522, 523, 524, 529, 530}
)


class ModelUnavailableError(httpx.HTTPError):
    """上游模型服务不可用：限流、超时、连接失败或网关错误。

    与“模型已响应但内容不合规”区分开：前者属于临时故障，应提示用户稍后重试；
    后者属于内容问题。上层据此给出可读提示，而不是把内部错误码抛给用户。

    继承 ``httpx.HTTPError`` 是为了让既有的“上游网关故障 → 502”判断保持不变。
    """

    def __init__(self, message: str, *, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def is_model_unavailable_error(exc: BaseException) -> bool:
    """判断异常是否代表“上游模型服务不可用”。

    同时覆盖已翻译的领域异常和尚未翻译的传输层异常，
    便于调用方在不需要改变异常类型的前提下分类失败原因。
    """
    if isinstance(exc, ModelUnavailableError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _UNAVAILABLE_STATUS_CODES
    return isinstance(exc, (httpx.TimeoutException, httpx.TransportError))


def _as_model_unavailable(exc: Exception) -> Optional[ModelUnavailableError]:
    """把“带明确响应的上游不可用”翻译成 ModelUnavailableError。

    只处理 ``HTTPStatusError``：这类失败已经收到上游响应，语义明确；
    连接失败与超时保留原类型，避免破坏调用方基于 httpx 异常类型做的重试。
    """
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in _UNAVAILABLE_STATUS_CODES:
            return ModelUnavailableError(
                f"model service returned HTTP {status}", status_code=status
            )
    return None


def _guarded_stream(source: Iterator[str]) -> Iterator[str]:
    try:
        yield from source
    except Exception as exc:  # noqa: BLE001 - 无法归类时原样抛出
        translated = _as_model_unavailable(exc)
        if translated is None:
            raise
        raise translated from exc


def _model_service_guard(method: Callable[..., Any]) -> Callable[..., Any]:
    """把“模型服务不可用”从 httpx 异常翻译成领域异常。

    同时覆盖普通调用与流式调用：流式调用在迭代期间才真正发请求，
    因此返回生成器时还要再包一层。
    """

    @functools.wraps(method)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            result = method(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - 无法归类时原样抛出
            translated = _as_model_unavailable(exc)
            if translated is None:
                raise
            raise translated from exc
        if inspect.isgenerator(result):
            return _guarded_stream(result)
        return result

    return wrapper


# ---------------------------------------------------------------------------
# OpenAI ↔ Anthropic 消息/工具格式转换工具
# 现有工作流基于 OpenAI 风格（messages、tools、tool_calls），
# API 模式走 Anthropic 兼容端点，需要在此做透明转换。
# ---------------------------------------------------------------------------

_DATA_URL_RE = re.compile(r"data:([^;]+);base64,(.*)", re.S)


def _parse_data_url(url: str) -> Optional[Tuple[str, str]]:
    match = _DATA_URL_RE.match(url or "")
    if not match:
        return None
    return match.group(1), match.group(2)


def _openai_content_to_anthropic(content: Any) -> List[Dict[str, Any]]:
    """OpenAI content（str 或 block 列表）→ Anthropic content blocks。"""
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content else [{"type": "text", "text": ""}]
    blocks: List[Dict[str, Any]] = []
    for item in content or []:
        if not isinstance(item, dict):
            continue
        block_type = item.get("type")
        if block_type == "text":
            blocks.append({"type": "text", "text": item.get("text", "")})
        elif block_type == "image_url":
            url = (item.get("image_url") or {}).get("url", "")
            parsed = _parse_data_url(url)
            if parsed:
                media_type, data = parsed
                blocks.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": media_type, "data": data},
                })
    return blocks or [{"type": "text", "text": ""}]


def _openai_tools_to_anthropic(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """OpenAI tools → Anthropic tools（name/description/input_schema）。"""
    result: List[Dict[str, Any]] = []
    for tool in tools or []:
        fn = tool.get("function") or {}
        result.append({
            "name": fn.get("name", ""),
            "description": fn.get("description", ""),
            "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
        })
    return result


def _openai_tool_choice_to_anthropic(choice: Any) -> Optional[Dict[str, Any]]:
    if choice is None or choice == "none":
        return None
    if choice == "auto":
        return {"type": "auto"}
    if choice == "required":
        return {"type": "any"}
    if isinstance(choice, dict) and choice.get("type") == "function":
        return {"type": "tool", "name": (choice.get("function") or {}).get("name", "")}
    return {"type": "auto"}


def _convert_messages(messages: List[Dict[str, Any]]) -> Tuple[Optional[str], List[Dict[str, Any]]]:
    """OpenAI messages → (system, anthropic_messages)。

    - system 角色抽取为顶层 system 字段；
    - 连续 role=tool 消息合并为一条 user 消息，内含多个 tool_result block；
    - assistant.tool_calls 转为 tool_use block。
    """
    system_parts: List[str] = []
    converted: List[Dict[str, Any]] = []
    pending_tool_results: List[Dict[str, Any]] = []

    def flush_tool_results():
        nonlocal pending_tool_results
        if pending_tool_results:
            converted.append({"role": "user", "content": pending_tool_results})
            pending_tool_results = []

    for msg in messages:
        role = msg.get("role")
        if role == "system":
            content = msg.get("content")
            if isinstance(content, str):
                system_parts.append(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        system_parts.append(block.get("text", ""))
            continue
        if role == "tool":
            pending_tool_results.append({
                "type": "tool_result",
                "tool_use_id": msg.get("tool_call_id", "") or "",
                "content": msg.get("content", "") or "",
            })
            continue
        flush_tool_results()
        content_blocks = _openai_content_to_anthropic(msg.get("content"))
        if role == "assistant":
            blocks = list(content_blocks)
            for tool_call in msg.get("tool_calls") or []:
                fn = tool_call.get("function") or {}
                try:
                    tool_input = json.loads(fn.get("arguments") or "{}")
                except Exception:
                    tool_input = {"query": fn.get("arguments", "")}
                blocks.append({
                    "type": "tool_use",
                    "id": tool_call.get("id", "") or "",
                    "name": fn.get("name", "") or "",
                    "input": tool_input,
                })
            converted.append({"role": "assistant", "content": blocks})
        else:  # user
            converted.append({"role": "user", "content": content_blocks})
    flush_tool_results()

    system = "\n\n".join(part for part in system_parts if part).strip() or None
    return system, converted


def _anthropic_response_to_message(data: Dict[str, Any]) -> Dict[str, Any]:
    """Anthropic 响应 → OpenAI 风格 message（content + tool_calls）。"""
    text_parts: List[str] = []
    tool_calls: List[Dict[str, Any]] = []
    for block in data.get("content") or []:
        block_type = block.get("type")
        if block_type == "text":
            text_parts.append(block.get("text", ""))
        elif block_type == "tool_use":
            tool_calls.append({
                "id": block.get("id", "") or "",
                "type": "function",
                "function": {
                    "name": block.get("name", "") or "",
                    "arguments": json.dumps(block.get("input") or {}, ensure_ascii=False),
                },
            })
    return {"content": "".join(text_parts), "tool_calls": tool_calls or None}


class LLMClient:
    """统一 LLM 客户端。

    - mode="api"：走 Anthropic 兼容端点（如 DeepSeek /anthropic），用 /v1/messages 协议；
    - mode="local"：走原 vLLM OpenAI 兼容服务，用 /chat/completions 协议。

    对外暴露 OpenAI 风格的 chat / chat_message / chat_stream 接口，
    工作流层无需感知底层协议差异，便于在 API 与本地部署间切换。
    """

    def __init__(self, base_url: str, model: str, api_key: Optional[str] = None, mode: str = "api"):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.mode = mode
        self.provider_session = new_upload_session()

    def _openai_headers(self) -> Dict[str, str]:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        headers.update(upload_provider_headers(self.base_url, self.provider_session))
        return headers

    def _is_api(self) -> bool:
        return self.mode == "api"

    def chat(self, messages: List[Dict[str, Any]], temperature: float = 0.2, max_tokens: int = 2048, extra_body: Optional[Dict[str, Any]] = None) -> str:
        message = self.chat_message(messages, temperature=temperature, max_tokens=max_tokens, extra_body=extra_body)
        return message.get("content") or ""

    @_model_service_guard
    def chat_message(self, messages: List[Dict[str, Any]], temperature: float = 0.2, max_tokens: int = 2048, extra_body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if self._is_api():
            return self._api_chat(messages, temperature, max_tokens, extra_body)
        return self._local_chat(messages, temperature, max_tokens, extra_body)

    @_model_service_guard
    def chat_stream(self, messages: List[Dict[str, Any]], temperature: float = 0.2, max_tokens: int = 2048, extra_body: Optional[Dict[str, Any]] = None) -> Iterator[str]:
        if self._is_api():
            yield from self._api_stream(messages, temperature, max_tokens, extra_body)
        else:
            yield from self._local_stream(messages, temperature, max_tokens, extra_body)

    # --- 本地 vLLM OpenAI 兼容协议 ---

    def _local_chat(self, messages: List[Dict[str, Any]], temperature: float, max_tokens: int, extra_body: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": int(max_tokens),
        }
        if extra_body:
            payload.update(extra_body)
        headers = self._openai_headers()
        with httpx.Client(timeout=LLM_TIMEOUT_SECONDS) as client:
            res = client.post(
                f"{self.base_url}/chat/completions", json=payload, headers=headers
            )
            res.raise_for_status()
            data = res.json()
        _log_token_budget_truncation(
            (data.get("choices") or [{}])[0].get("finish_reason"), int(max_tokens)
        )
        return data["choices"][0]["message"]

    def _local_stream(self, messages: List[Dict[str, Any]], temperature: float, max_tokens: int, extra_body: Optional[Dict[str, Any]]) -> Iterator[str]:
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": int(max_tokens),
            "stream": True,
        }
        if extra_body:
            payload.update(extra_body)
        headers = self._openai_headers()
        with httpx.Client(timeout=LLM_TIMEOUT_SECONDS) as client:
            with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=headers,
            ) as res:
                res.raise_for_status()
                for line in res.iter_lines():
                    if not line:
                        continue
                    if line.startswith("data:"):
                        line = line[len("data:"):].strip()
                    if line == "[DONE]":
                        break
                    try:
                        data = json.loads(line)
                    except Exception:
                        continue
                    delta = (data.get("choices") or [{}])[0].get("delta") or {}
                    content = delta.get("content") or ""
                    if content:
                        yield content

    # --- 远程 API Anthropic 兼容协议 ---

    def _api_headers(self) -> Dict[str, str]:
        return {
            "x-api-key": self.api_key or "",
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

    def _api_payload(self, messages: List[Dict[str, Any]], temperature: float, max_tokens: int, extra_body: Optional[Dict[str, Any]], stream: bool) -> Dict[str, Any]:
        system, conv = _convert_messages(messages)
        payload: Dict[str, Any] = {
            "model": self.model,
            "max_tokens": int(max_tokens) if max_tokens else 4096,
            "temperature": temperature,
            "messages": conv,
        }
        if system:
            payload["system"] = system
        if stream:
            payload["stream"] = True
        if extra_body:
            tools = extra_body.get("tools")
            if tools:
                payload["tools"] = _openai_tools_to_anthropic(tools)
            anthropic_choice = _openai_tool_choice_to_anthropic(extra_body.get("tool_choice"))
            if anthropic_choice:
                payload["tool_choice"] = anthropic_choice
            # chat_template_kwargs 等 vLLM 专用参数在 API 模式下忽略。
        return payload

    def _api_chat(self, messages: List[Dict[str, Any]], temperature: float, max_tokens: int, extra_body: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        payload = self._api_payload(messages, temperature, max_tokens, extra_body, stream=False)
        with httpx.Client(timeout=LLM_TIMEOUT_SECONDS) as client:
            res = client.post(f"{self.base_url}/v1/messages", json=payload, headers=self._api_headers())
            res.raise_for_status()
            data = res.json()
        _log_token_budget_truncation(data.get("stop_reason"), int(payload["max_tokens"]))
        return _anthropic_response_to_message(data)

    def _api_stream(self, messages: List[Dict[str, Any]], temperature: float, max_tokens: int, extra_body: Optional[Dict[str, Any]]) -> Iterator[str]:
        payload = self._api_payload(messages, temperature, max_tokens, extra_body, stream=True)
        with httpx.Client(timeout=LLM_TIMEOUT_SECONDS) as client:
            with client.stream("POST", f"{self.base_url}/v1/messages", json=payload, headers=self._api_headers()) as res:
                res.raise_for_status()
                for line in res.iter_lines():
                    if not line:
                        continue
                    if line.startswith("data:"):
                        line = line[len("data:"):].strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except Exception:
                        continue
                    if event.get("type") == "content_block_delta":
                        delta = event.get("delta") or {}
                        if delta.get("type") == "text_delta":
                            text = delta.get("text") or ""
                            if text:
                                yield text


# 向后兼容别名：旧代码仍可 from health_llm import VLLMClient。
VLLMClient = LLMClient


def build_llm_client(role: str = "planner") -> LLMClient:
    """根据 config.LLM_MODE 构建 LLM 客户端。

    role 仅在 local 模式下决定使用哪一组 vLLM 配置：
    - "planner" / "executor"：Planner/Executor 模型；
    - 其他（含 "manager" / "reviewer"）：Manager/Reviewer 模型。
    api 模式下所有角色共用同一远程模型。
    """
    from APP.backend.config import (
        LLM_MODE, LLM_API_KEY, LLM_API_BASE_URL, LLM_API_MODEL,
        PLANNER_EXECUTOR_BASE_URL, PLANNER_EXECUTOR_MODEL,
        MANAGER_REVIEWER_BASE_URL, MANAGER_REVIEWER_MODEL,
    )
    if LLM_MODE == "local":
        if role in ("planner", "executor"):
            return LLMClient(
                PLANNER_EXECUTOR_BASE_URL,
                PLANNER_EXECUTOR_MODEL,
                api_key=LLM_API_KEY,
                mode="local",
            )
        return LLMClient(
            MANAGER_REVIEWER_BASE_URL,
            MANAGER_REVIEWER_MODEL,
            api_key=LLM_API_KEY,
            mode="local",
        )
    return LLMClient(LLM_API_BASE_URL, LLM_API_MODEL, api_key=LLM_API_KEY, mode="api")
