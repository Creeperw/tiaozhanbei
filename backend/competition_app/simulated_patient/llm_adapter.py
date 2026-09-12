"""
LLM 适配器 — 使用项目在 .env.local 中配置的 DashScope 模型。

实现 simulated_patient.llm_provider.LLMProvider 的同步 chat() 接口。
在同步上下文中直接通过 httpx 调用 LLM，避免 async/sync 桥接问题。
"""

from __future__ import annotations

import logging
from typing import List, Dict, Optional
from uuid import uuid4

import httpx

from ..llm.provider_session import current_provider_session
from ..llm.upload_provider import upload_provider_headers
from .llm_provider import LLMProvider

logger = logging.getLogger(__name__)


class ProjectLLMProvider(LLMProvider):
    """适配器：将项目 LLM 配置包装为组件的同步 LLMProvider 接口。"""

    def __init__(self, *, timeout_seconds: float = 60.0):
        # 从项目配置中读取
        from competition_app.config import Settings
        settings = Settings.from_env()
        self._base_url: str = settings.chat_base_url.rstrip("/")
        self._api_key: str = settings.llm_api_key
        self._model: str = settings.chat_model
        self._timeout: float = timeout_seconds
        # opencode.ai 需要稳定的会话标识才能路由请求；按 provider 实例复用同一个。
        self._provider_session: str = current_provider_session() or f"tcm-sp-{uuid4().hex}"

    def _provider_headers(self) -> Dict[str, str]:
        headers = {"Authorization": f"Bearer {self._api_key}"}
        headers.update(upload_provider_headers(self._base_url, self._provider_session))
        return headers

    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 500,
        **kwargs,
    ) -> Optional[str]:
        """同步调用 LLM 对话。

        将 OpenAI 格式 messages 直接转发到项目配置的 OpenAI-compatible 端点。
        """
        # 转换 role：某些端点不支持 system role
        formatted = []
        for m in messages:
            role = m["role"]
            if role == "system":
                role = "user"
            formatted.append({"role": role, "content": m["content"]})

        request_body = {
            "model": self._model,
            "messages": formatted,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        # Qwen3 模型禁用 thinking 以避免推理文本混入回复
        if self._model.lower().startswith("qwen3"):
            request_body["enable_thinking"] = False

        try:
            with httpx.Client(timeout=self._timeout) as client:
                resp = client.post(
                    f"{self._base_url}/chat/completions",
                    headers=self._provider_headers(),
                    json=request_body,
                )
                resp.raise_for_status()
                body = resp.json()
                return body["choices"][0]["message"]["content"]
        except Exception:
            # 保留返回 None 的契约，但必须留下日志：静默吞掉异常会让上层把
            # “调用失败”当成“模型没有输出”，进而回退到与提问无关的兜底文案。
            logger.warning(
                "simulated-patient LLM call failed (model=%s, base_url=%s)",
                self._model,
                self._base_url,
                exc_info=True,
            )
            return None
