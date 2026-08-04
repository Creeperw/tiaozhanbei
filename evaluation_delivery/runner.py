"""进程内评测运行器。

在评测进程中直接构建 ApplicationContainer 并调用 use_case.execute()，
拿到结构化的 ReviewCardResult（资源/审核/快照/模型轨迹），避免走
HTTP+SSE 的解析成本与不稳定。

关键点：
- 与主服务进程完全隔离（只读导入框架包，不共享内存状态）
- 结果按 case 一行 JSONL 落盘，支持断点续跑
- 并发由 asyncio.Semaphore 控制，默认 3 workers
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# 框架包根：backend/competition_app
_BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from competition_app.application.personalized_review_card import (  # noqa: E402
    ReviewCardRequest,
)


@dataclass
class RunConfig:
    """一次评测的运行配置。"""

    learner_id: str = "eval-user"
    available_minutes: int = 15
    workers: int = 3
    output_dir: Path = field(default_factory=lambda: Path(__file__).parent / "outputs")
    env_file: Path | None = None  # 指向包含 API keys 的 env 文件（默认 backend .env.local）
    profile: dict[str, Any] = field(default_factory=dict)  # user_profile 注入
    learning_profile: dict[str, Any] = field(default_factory=dict)
    user_knowledge_state: list[dict[str, Any]] = field(default_factory=list)
    question_attempt: list[dict[str, Any]] = field(default_factory=list)
    extra_params: dict[str, Any] = field(default_factory=dict)
    retry_attempts: int = 2  # 模型偶发错误重试次数
    retry_delay_seconds: float = 10.0


class EvaluationRunner:
    """一次评测会话：构建容器 → 逐 case 执行 → 落盘。"""

    def __init__(self, config: RunConfig) -> None:
        self.config = config
        self.config.output_dir = Path(config.output_dir)
        self.container = None
        self._semaphore = asyncio.Semaphore(config.workers)
        self._done_ids: set[str] = set()

    # ---------- 生命周期 ----------

    def load_settings(self):
        """读取配置构建 Settings（live 模式需要 API keys）。"""
        from competition_app.config import Settings

        if self.config.env_file and self.config.env_file.is_file():
            import os as _os

            values: dict[str, str] = {}
            for raw in self.config.env_file.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip("'\"")
            return Settings.from_env(values)
        return Settings.from_env()

    def build_container(self):
        """构建应用容器（live 模式 → 真实模型链路）。"""
        from competition_app.application.container import ApplicationContainer

        settings = self.load_settings()
        if settings.mode != "live":
            raise RuntimeError(
                "评测需要 live 模式（真实模型链路）。请检查 env 中 COMPETITION_APP_MODE=live "
                "并配置 DASHSCOPE_API_KEY / SILICONFLOW_API_KEY。"
            )
        self.container = ApplicationContainer.build(
            settings,
            snapshot_root=self.config.output_dir / "snapshots",
            stream_model_output=False,
        )
        return self.container

    def use_case(self):
        return self.container.review_card_use_case

    # ---------- 执行 ----------

    async def run_case(self, case: dict[str, Any]) -> dict[str, Any]:
        """执行单个用例，返回归一化结果。

        case 必填字段：
          case_id     唯一标识（断点续跑依据）
          user_request 发送给框架的用户请求
        可选字段：
          expected    期望答案/黄金标签（用于判定）
          kp_ids      知识点（覆盖率检索口径）
          meta        附加元数据（透传落盘）
        """
        case_id = str(case.get("case_id") or f"case-{uuid4_hex()}")
        if case_id in self._done_ids:
            return {"case_id": case_id, "skipped": True}

        request = ReviewCardRequest(
            thread_id=f"THREAD_{uuid4_hex()}",
            conversation_id=f"CONV_{uuid4_hex()}",
            learner_id=self.config.learner_id,
            user_request=str(case.get("user_request", "")),
            available_minutes=self.config.available_minutes,
            user_profile=self.config.profile,
            learning_profile=self.config.learning_profile,
            user_knowledge_state=self.config.user_knowledge_state,
            question_attempt=self.config.question_attempt,
            **self.config.extra_params,
        )
        started = time.time()
        max_attempts = self.config.retry_attempts + 1
        for attempt in range(1, max_attempts + 1):
            try:
                result = await self.use_case().execute(request)
                record = self._normalize_result(case_id, case, result, started)
                record["attempt"] = attempt
                return record
            except Exception as exc:  # noqa: BLE001 —— 评测必须捕获全部异常落盘
                error_type = type(exc).__name__
                error_message = str(exc)
                retryable = self._is_retryable(error_type, error_message)
                if attempt < max_attempts and retryable:
                    print(
                        f"[runner] {case_id} 第 {attempt} 次失败({error_type})，可重试，稍后重试…",
                        file=sys.stderr,
                    )
                    await asyncio.sleep(self.config.retry_delay_seconds)
                    continue
                return {
                    "case_id": case_id,
                    "status": "failed",
                    "error_type": error_type,
                    "error_message": error_message[:500],
                    "retryable": retryable,
                    "attempt": attempt,
                    "duration_seconds": round(time.time() - started, 2),
                    "input": case,
                }
        raise RuntimeError("unreachable")  # pragma: no cover

    @staticmethod
    def _is_retryable(error_type: str, error_message: str) -> bool:
        """模型偶发空响应/超时/限流属于可重试错误。"""
        text = f"{error_type} {error_message}"
        return any(
            kw in text
            for kw in (
                "empty content",
                "empty response",
                "ModelResponseError",
                "timeout",
                "rate limit",
                "429",
                "503",
                "ConnectionError",
                "ReadTimeout",
            )
        )

    def _normalize_result(self, case_id: str, case: dict[str, Any], result, started: float) -> dict[str, Any]:
        """ReviewCardResult → 可 JSON 序列化字典（保留评测必需字段）。"""
        record: dict[str, Any] = {
            "case_id": case_id,
            "status": getattr(result, "status", "unknown"),
            "task_type": getattr(result, "task_type", None),
            "execution_id": getattr(result, "execution_id", None),
            "duration_seconds": round(time.time() - started, 2),
            "input": case,
        }
        resource = getattr(result, "resource", None)
        if resource is not None:
            claims = []
            for c in (getattr(resource, "claims", None) or []):
                # ResourceClaim 为 dataclass 对象（非 dict）
                claims.append(
                    {
                        "text": getattr(c, "text", None) if not isinstance(c, dict) else c.get("text"),
                        "evidence_ids": getattr(c, "evidence_ids", None) if not isinstance(c, dict) else c.get("evidence_ids", []),
                    }
                )
            record["resource"] = {
                "title": getattr(resource, "title", None),
                "content": getattr(resource, "content", None),
                "estimated_minutes": getattr(resource, "estimated_minutes", None),
                "claims": claims,
            }
        audit = getattr(result, "audit", None)
        if audit is not None:
            record["audit"] = {
                "decision": getattr(audit, "decision", None),
                "findings": list(getattr(audit, "findings", None) or []),
                "verified_claim_ids": list(getattr(audit, "verified_claim_ids", None) or []),
            }
        snapshot = getattr(result, "snapshot_path", None)
        record["snapshot_path"] = str(snapshot) if snapshot else None
        # 模型轨迹摘要（供失败追责，不落全量 payload）
        trace = getattr(result, "model_trace", None) or []
        record["model_trace"] = [
            {
                "sequence": getattr(t, "sequence", None),
                "agent": getattr(t, "agent", None),
                "error_type": getattr(t, "error_type", None),
                "model": getattr(t, "model", None),
            }
            for t in trace[-12:]
        ]
        if isinstance(result, dict) and result.get("status") == "interrupted":
            record["interrupt"] = result.get("interrupt")
        return record

    async def run_cases(self, cases: list[dict[str, Any]], run_name: str = "run") -> Path:
        """并发执行全部用例并落盘 JSONL，返回输出文件路径。

        - 支持断点续跑：同 run_name 的既有结果会被加载，已完成 case 跳过
        - 实时进度打印到 stderr
        """
        out_dir = self.config.output_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{run_name}.jsonl"
        self._done_ids = self._load_done_ids(out_path)

        total = len(cases)
        pending = [c for c in cases if str(c.get("case_id")) not in self._done_ids]
        print(f"[runner] {run_name}: 共 {total} 用例，已完成 {total - len(pending)}，待执行 {len(pending)}", file=sys.stderr)

        async def worker(case: dict[str, Any]) -> dict[str, Any]:
            async with self._semaphore:
                return await self.run_case(case)

        results: list[dict[str, Any]] = []
        done = 0
        for idx in range(0, len(pending), self.config.workers):
            batch = pending[idx : idx + self.config.workers]
            batch_results = await asyncio.gather(*(worker(c) for c in batch))
            for record in batch_results:
                results.append(record)
                self._append_record(out_path, record)
                done += 1
                status = record.get("status", "?")
                print(f"[runner] {done}/{len(pending)} {record.get('case_id')} → {status}", file=sys.stderr)
        return out_path

    # ---------- 持久化 ----------

    @staticmethod
    def _load_done_ids(out_path: Path) -> set[str]:
        if not out_path.is_file():
            return set()
        ids: set[str] = set()
        for line in out_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ids.add(str(json.loads(line).get("case_id")))
            except json.JSONDecodeError:
                continue
        return ids

    @staticmethod
    def _append_record(out_path: Path, record: dict[str, Any]) -> None:
        with out_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def uuid4_hex() -> str:
    return uuid.uuid4().hex
