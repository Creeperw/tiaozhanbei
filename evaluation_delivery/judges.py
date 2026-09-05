"""判定通道：规则比对、LLM Judge、Embedding 语义匹配。

三份方案共用的双通道判定：
- 通道 A（规则/确定性）：客观题字母比对、难度区间、余弦阈值
- 通道 B（LLM Judge）：DeepSeek 直连，CoT + 冻结 prompt 版本
- A/B 一致 → 采纳；冲突 → 标 "conflict" 待人工复核

Judge 与 embedding 均 httpx 直连，不依赖框架模型栈，评测人员可自由切换。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any, Sequence

import httpx

# ---------- 配置 ----------

JUDGE_BASE_URL = os.environ.get("EVAL_JUDGE_BASE_URL", "https://api.deepseek.com")
JUDGE_API_KEY = os.environ.get("EVAL_JUDGE_API_KEY", "")
JUDGE_MODEL = os.environ.get("EVAL_JUDGE_MODEL", "deepseek-v4-flash")
JUDGE_TIMEOUT = float(os.environ.get("EVAL_JUDGE_TIMEOUT", "60"))

EMBED_BASE_URL = os.environ.get("EVAL_EMBED_BASE_URL", "https://api.siliconflow.cn/v1")
EMBED_API_KEY = os.environ.get("EVAL_EMBED_API_KEY", "")
EMBED_MODEL = os.environ.get("EVAL_EMBED_MODEL", "Qwen/Qwen3-Embedding-4B")

# Judge prompt 冻结版本号——随报告提交，改 prompt 必须升版本
JUDGE_PROMPT_VERSION = "1.0"


# ---------- 通道 A：规则 ----------

def extract_answer_letter(text: str) -> str | None:
    """从输出中提取选项字母（A/B/C/D/E）。

    优先匹配「答案[:：]A」等模式；退化为全文扫描首字母。
    """
    if not text:
        return None
    patterns = [
        r"[答选]案\s*[:：]\s*([A-E])",
        r"^\(?([A-E])\)?[.、．\s]",
        r"选\s*([A-E])",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.MULTILINE)
        if m:
            return m.group(1).upper()
    # 全文最后一个单独出现的字母（兜底）
    letters = re.findall(r"\b([A-E])\b", text)
    return letters[-1].upper() if letters else None


def answer_letter_match(system_answer: str, expected: str | list[str] | None) -> bool:
    got = extract_answer_letter(system_answer or "")
    if isinstance(expected, list):  # 题库 answer 格式为 ['C']
        want = {str(x).strip().upper() for x in expected if x}
    else:
        want = {str(expected or "").strip().upper()} - {""}
    return bool(got and got in want)


def in_difficulty_range(difficulty: int | None, target_low: int, target_high: int) -> bool:
    """难度标签是否落在画像目标区间（难度 1-5）。"""
    return difficulty is not None and target_low <= int(difficulty) <= target_high


# ---------- 通道 B：LLM Judge ----------

class LLMJudge:
    """DeepSeek 直连判定器。每条判 2 次多数决。"""

    def __init__(
        self,
        base_url: str = JUDGE_BASE_URL,
        api_key: str = JUDGE_API_KEY,
        model: str = JUDGE_MODEL,
        timeout: float = JUDGE_TIMEOUT,
        api_keys: Sequence[str] | None = None,
        max_concurrent_requests: int = 4,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._api_keys = tuple(
            dict.fromkeys(
                key.strip()
                for key in (api_keys or (api_key,))
                if isinstance(key, str) and key.strip()
            )
        )
        self.api_key = self._api_keys[0] if self._api_keys else ""
        self._active_api_key_index = 0
        self._api_key_state_lock = asyncio.Lock()
        self._request_semaphore = asyncio.Semaphore(max(1, int(max_concurrent_requests)))
        self.model = model
        self.timeout = timeout
        self.transport = transport
        self.version = JUDGE_PROMPT_VERSION

    async def _current_api_key(self) -> tuple[int, str]:
        async with self._api_key_state_lock:
            if self._active_api_key_index >= len(self._api_keys):
                raise RuntimeError("所有配置的 LLM Judge API Key 均已余额或配额耗尽")
            index = self._active_api_key_index
            return index, self._api_keys[index]

    async def _retire_exhausted_api_key(self, index: int) -> bool:
        async with self._api_key_state_lock:
            if self._active_api_key_index <= index:
                self._active_api_key_index = index + 1
            return self._active_api_key_index < len(self._api_keys)

    @staticmethod
    def _is_explicit_quota_exhaustion(response: httpx.Response) -> bool:
        if response.status_code == 402:
            return True
        message = response.text.lower()
        return any(
            marker in message
            for marker in (
                "insufficient balance",
                "balance is insufficient",
                "insufficient quota",
                "quota exhausted",
                "credits exhausted",
                "out of credits",
                "余额不足",
                "额度不足",
                "配额耗尽",
            )
        )

    async def _call(self, system: str, user: str) -> dict[str, Any]:
        if not self._api_keys:
            raise RuntimeError("EVAL_JUDGE_API_KEY 未配置，无法调用 LLM Judge")
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.2,
        }
        async with self._request_semaphore:
            async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
                while True:
                    api_key_index, api_key = await self._current_api_key()
                    resp = await client.post(
                        f"{self.base_url}/chat/completions",
                        headers={"Authorization": f"Bearer {api_key}"},
                        json=payload,
                    )
                    if self._is_explicit_quota_exhaustion(resp):
                        if await self._retire_exhausted_api_key(api_key_index):
                            continue
                        raise RuntimeError("所有配置的 LLM Judge API Key 均已余额或配额耗尽")
                    resp.raise_for_status()
                    content = resp.json()["choices"][0]["message"].get("content") or ""
                    break
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return {"parse_error": content[:200]}

    async def judge(self, system: str, user: str, rounds: int = 2) -> list[dict[str, Any]]:
        """同输入判 rounds 次，返回每次的原始结果。"""
        return [await self._call(system, user) for _ in range(rounds)]

    def majority(self, results: list[dict[str, Any]], key: str = "is_correct") -> tuple[Any, int]:
        """多数决：返回 (多数值, 一致票数)。票数=rounds 表示一致。"""
        votes: dict[Any, int] = {}
        for r in results:
            v = r.get(key)
            if isinstance(v, (bool, str)):
                votes[str(v)] = votes.get(str(v), 0) + 1
        if not votes:
            return None, 0
        top = max(votes, key=votes.get)
        return top, votes[top]


class EmbeddingMatcher:
    """SiliconFlow embedding 余弦相似度匹配（覆盖率通道 A）。"""

    def __init__(
        self,
        base_url: str = EMBED_BASE_URL,
        api_key: str = EMBED_API_KEY,
        model: str = EMBED_MODEL,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

    async def _embed(self, text: str) -> list[float]:
        if not self.api_key:
            raise RuntimeError("EVAL_EMBED_API_KEY 未配置，无法调用 embedding")
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self.base_url}/embeddings",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"model": self.model, "input": text},
            )
            resp.raise_for_status()
            return resp.json()["data"][0]["embedding"]

    async def similarity(self, a: str, b: str) -> float:
        va, vb = await asyncio.gather(self._embed(a), self._embed(b))
        n = sum(x * x for x in va) ** 0.5
        m = sum(x * x for x in vb) ** 0.5
        if not n or not m:
            return 0.0
        return sum(x * y for x, y in zip(va, vb)) / (n * m)
