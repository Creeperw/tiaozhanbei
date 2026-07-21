from __future__ import annotations

import json
from typing import Any, Literal

from competition_app.runtime.snapshot import _sanitize


class TerminalTrace:
    """Human-readable, secret-safe boundaries for an interactive agent run."""

    def __init__(self, enabled: bool = False, level: Literal["summary", "model", "full"] = "model") -> None:
        self.enabled = enabled
        self.level = level

    def model_input(self, agent: str, payload: dict[str, Any]) -> None:
        if self.level == "full":
            self._print(agent, "模型输入", payload)

    def model_output(self, agent: str, payload: dict[str, Any]) -> None:
        if self.level in {"model", "full"}:
            self._print(agent, "模型原始输出", payload)

    def validation(self, agent: str, *, valid: bool, detail: str) -> None:
        if not self.enabled:
            return
        state = "通过" if valid else "失败"
        print(f"[{agent}] 协议校验: {state} ({detail})", flush=True)

    def system_output(self, agent: str, payload: Any) -> None:
        if self.level == "full":
            self._print(agent, "系统产物", payload)

    def tool_event(self, agent: str, payload: dict[str, Any]) -> None:
        if self.level == "full":
            self._print(agent, "工具调用摘要", payload)

    def _print(self, agent: str, label: str, payload: Any) -> None:
        if not self.enabled:
            return
        safe = _sanitize(payload)
        print(
            f"[{agent}] {label}\n"
            + json.dumps(safe, ensure_ascii=False, separators=(",", ":")),
            flush=True,
        )