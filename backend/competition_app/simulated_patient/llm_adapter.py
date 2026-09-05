"""
LLM 适配器 — 使用项目在 .env.local 中配置的 DashScope 模型。

实现 simulated_patient.llm_provider.LLMProvider 的同步 chat() 接口。
在同步上下文中直接通过 httpx 调用 LLM，避免 async/sync 桥接问题。
"""

from __future__ import annotations

from typing import List, Dict, Optional

import httpx

from .llm_provider import LLMProvider


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
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json=request_body,
                )
                resp.raise_for_status()
                body = resp.json()
                return body["choices"][0]["message"]["content"]
        except Exception:
            return None
