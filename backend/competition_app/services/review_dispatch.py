"""队列级到期复习派发。

把「已到期但尚未物化任务/资源的知识点」从智能体工具的一次性副作用，提升为
面向复习队列的后台任务：只要复习队列被读取（用户浏览界面即可），后台就推进
一轮有界的资源物化，直到队列没有可推进的候选或达到单轮预算。

三条硬约束都来自此前的线上缺陷：

1. **不能只取队首候选。** 旧实现用 ``next(...)`` 取队列第一条，该知识点一旦
   生成失败就被永久钉死，排在它后面的真实知识点永远轮不到；本模块按队列顺序
   **遍历候选**，单个知识点失败只记冷却，继续尝试下一个。
2. **不能静默吞异常。** 旧实现是 ``except Exception: pass``，失败在日志里不留
   任何痕迹，只能靠人工发现「队列一直没资源」；本模块把失败写进日志与状态字典。
3. **不能无限烧模型调用。** 队列级触发意味着每次读取队列都可能启动后台任务，
   因此单轮物化数量、同一学习者两次运行的最小间隔、失败项冷却都设了上限。

调用方只需 ``request()``（同步、非阻塞）触发，或 ``await run()`` 直接推进一轮
（测试与显式派发端点用）。
"""

from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from competition_app.contracts.review import ReviewMemoryUnit, ReviewQueueEntry
from competition_app.services.review import ReviewService


logger = logging.getLogger(__name__)

#: 生成失败的知识点在该时长内不再重试，避免每次刷新页面都重复烧一次模型调用。
DEFAULT_FAILURE_COOLDOWN = timedelta(minutes=30)

#: 同一学习者两次派发之间的最小间隔。队列级触发下，这是控制模型成本的主要闸门。
DEFAULT_MIN_RUN_INTERVAL = timedelta(seconds=120)

#: 单轮最多物化多少个资源。到期项可能有几十个，一次性全部生成会挤占正常请求。
DEFAULT_MAX_MATERIALIZE_PER_RUN = 3

#: 复习卡生成返回的失败态：``failed`` 是业务失败，``interrupted`` 是流程意外进入
#: 追问（后台无人应答）。两者都不能当成功，必须换下一个候选继续。
_UNSUCCESSFUL_RESULT_STATUSES = frozenset({"failed", "interrupted"})

#: ``prompt_abstract`` 里残留的资源后缀，派发前需要剥掉才能还原知识点名称。
_TOPIC_SUFFIXES = ("个性化复习卡", "个性化练习", "复习卡片", "复习卡")

#: 不能当作知识点名称的占位符。
_PLACEHOLDER_TOPICS = frozenset(
    {"", "知识点名称待补充", "待补充知识点", "知识点待确认"}
)


def dashboard_kp_name_loader(
    load_dashboard: Callable[..., Any],
) -> Callable[[str], dict[str, str]]:
    """Build a kp_id→kp_name resolver from the review dashboard loader.

    ``prompt_abstract`` is not always populated with a usable name (older rows
    store the raw ``kp_id``), so dispatch falls back to the mastery snapshots
    the dashboard already knows how to resolve.
    """

    def load(learner_id: str) -> dict[str, str]:
        dashboard = load_dashboard(learner_id, history_limit=1)
        names: dict[str, str] = {}
        for collection in ("mastery", "review_states"):
            for item in dashboard.get(collection) or []:
                kp_id = str(item.get("kp_id") or "").strip()
                kp_name = str(item.get("kp_name") or "").strip()
                if kp_id and kp_name and kp_name != kp_id:
                    names[kp_id] = kp_name
        return names

    return load


class DueReviewDispatcher:
    """把到期复习知识点物化成可立即学习的复习资源（队列级后台任务）。"""

    def __init__(
        self,
        *,
        review_service: ReviewService,
        review_card_use_case: Any,
        kp_name_loader: Callable[[str], dict[str, str]] | None = None,
        max_materialize_per_run: int = DEFAULT_MAX_MATERIALIZE_PER_RUN,
        min_run_interval: timedelta = DEFAULT_MIN_RUN_INTERVAL,
        failure_cooldown: timedelta = DEFAULT_FAILURE_COOLDOWN,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._review_service = review_service
        self._review_card_use_case = review_card_use_case
        self._kp_name_loader = kp_name_loader
        self._max_materialize_per_run = max(1, int(max_materialize_per_run))
        self._min_run_interval = min_run_interval
        self._failure_cooldown = failure_cooldown
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._guard = threading.Lock()
        self._running: set[str] = set()
        self._tasks: set[asyncio.Task] = set()
        self._last_started: dict[str, datetime] = {}
        self._failures: dict[tuple[str, str], datetime] = {}
        self._states: dict[str, dict[str, Any]] = {}

    # ── 触发侧 ────────────────────────────────────────────────────────────
    def request(self, learner_id: str, *, available_minutes: int = 15) -> bool:
        """Request one background dispatch run; ``False`` means it was skipped.

        Non-blocking by design: queue reads must never wait for model calls.
        Skips when the learner already has a run in flight or when the last run
        started inside ``min_run_interval``.
        """

        if not learner_id:
            return False
        now = self._clock()
        with self._guard:
            if learner_id in self._running:
                return False
            last_started = self._last_started.get(learner_id)
            if last_started is not None and now - last_started < self._min_run_interval:
                return False
            self._running.add(learner_id)
            self._last_started[learner_id] = now
            self._states[learner_id] = {
                "status": "running",
                "started_at": now.isoformat(),
            }

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # 同步上下文（CLI、无事件循环的测试）没有可挂载后台任务的地方。
            self._release(learner_id)
            self._update_state(learner_id, {"status": "skipped_no_event_loop"})
            return False

        async def _tracked() -> None:
            try:
                await self.run(learner_id, available_minutes=available_minutes)
            except Exception as exc:  # run() 内部已处理可预期失败，这里是兜底
                logger.exception(
                    "到期复习派发任务异常 learner=%s", learner_id
                )
                self._update_state(
                    learner_id,
                    {
                        "status": "failed",
                        "error_type": type(exc).__name__,
                        "message": str(exc)[:500],
                        "finished_at": self._clock().isoformat(),
                    },
                )
            finally:
                self._release(learner_id)
                current = asyncio.current_task()
                if current is not None:
                    with self._guard:
                        self._tasks.discard(current)

        task = loop.create_task(_tracked())
        with self._guard:
            self._tasks.add(task)
        return True

    def state(self, learner_id: str) -> dict[str, Any]:
        """Last known run state for one learner (observability, not control)."""

        with self._guard:
            return dict(self._states.get(learner_id) or {})

    def failure_count(self, learner_id: str) -> int:
        with self._guard:
            return sum(1 for key in self._failures if key[0] == learner_id)

    # ── 执行侧 ────────────────────────────────────────────────────────────
    async def run(
        self,
        learner_id: str,
        *,
        available_minutes: int = 15,
        max_materialize: int | None = None,
    ) -> dict[str, Any]:
        """Advance one bounded round of queue-level dispatch.

        Returns a structured result rather than raising, so both the background
        task and the explicit dispatch endpoint can map it onto their own
        contract (the endpoint turns ``interrupted`` back into a 409).
        """

        budget = self._max_materialize_per_run if max_materialize is None else max(1, int(max_materialize))
        try:
            candidates = await asyncio.to_thread(
                self._review_service.dispatch_candidates, learner_id
            )
        except Exception as exc:
            logger.warning(
                "到期复习派发读取队列失败 learner=%s: %s", learner_id, exc
            )
            result = {
                "status": "failed",
                "reason": "queue_unavailable",
                "error_type": type(exc).__name__,
                "materialized": [],
                "skipped": [],
            }
            self._finish(learner_id, result)
            return result

        if not candidates:
            result = {
                "status": "idle",
                "reason": "no_due_candidate",
                "message": "当前没有等待资源的到期复习知识点。",
                "materialized": [],
                "skipped": [],
            }
            self._finish(learner_id, result)
            return result

        kp_names = await self._load_kp_names(learner_id)
        materialized: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        interrupted = False

        for entry in candidates:
            if len(materialized) >= budget:
                break
            unit = entry.memory_unit
            kp_id = str(unit.kp_id or "").strip()

            cooldown = self._cooldown_remaining(learner_id, kp_id)
            if cooldown is not None:
                skipped.append(
                    {
                        "kp_id": kp_id,
                        "reason": "failure_cooldown",
                        "retry_after_seconds": int(cooldown.total_seconds()),
                    }
                )
                continue

            topic = self._dispatch_topic(entry, kp_names)
            if not topic:
                self._record_failure(learner_id, kp_id)
                skipped.append({"kp_id": kp_id, "reason": "unresolved_topic"})
                logger.warning(
                    "到期复习派发跳过无可用名称的知识点 learner=%s kp=%s",
                    learner_id,
                    kp_id,
                )
                continue

            try:
                outcome = await self._review_card_use_case.execute(
                    self._build_request(learner_id, unit, topic, available_minutes)
                )
            except Exception as exc:
                self._record_failure(learner_id, kp_id)
                skipped.append(
                    {
                        "kp_id": kp_id,
                        "reason": "generation_error",
                        "error_type": type(exc).__name__,
                    }
                )
                logger.warning(
                    "到期复习派发生成失败 learner=%s kp=%s: %s",
                    learner_id,
                    kp_id,
                    exc,
                )
                continue

            status = str(getattr(outcome, "status", "") or "")
            if status in _UNSUCCESSFUL_RESULT_STATUSES:
                self._record_failure(learner_id, kp_id)
                skipped.append({"kp_id": kp_id, "reason": f"generation_{status}"})
                logger.warning(
                    "到期复习派发未产出资源 learner=%s kp=%s status=%s",
                    learner_id,
                    kp_id,
                    status,
                )
                if status == "interrupted":
                    interrupted = True
                continue

            self._clear_failure(learner_id, kp_id)
            materialized.append(
                {
                    "kp_id": kp_id,
                    "topic": topic,
                    "result": (
                        outcome.model_dump(mode="json")
                        if hasattr(outcome, "model_dump")
                        else outcome
                    ),
                }
            )

        result = {
            "status": "materialized" if materialized else "blocked",
            "reason": (
                "materialized"
                if materialized
                else "all_candidates_skipped"
            ),
            "interrupted": interrupted,
            "materialized": materialized,
            "skipped": skipped,
            "remaining": max(0, len(candidates) - len(materialized)),
        }
        if not materialized and not interrupted:
            result["message"] = "到期知识点缺少可解析名称或生成失败，已跳过自动资源生成。"
        # 后台任务没有调用方可以查看返回值，必须自己把本轮结果写进日志；否则
        # “一轮什么都没产出”在线上是完全不可见的。
        if materialized:
            logger.info(
                "到期复习派发完成 learner=%s 物化=%d 跳过=%d 剩余=%d",
                learner_id,
                len(materialized),
                len(skipped),
                result["remaining"],
            )
        else:
            logger.warning(
                "到期复习派发未产出资源 learner=%s 跳过=%d 剩余=%d reasons=%s",
                learner_id,
                len(skipped),
                result["remaining"],
                ",".join(sorted({str(item.get("reason")) for item in skipped}))
                or "none",
            )
        self._finish(learner_id, result)
        return result

    # ── 内部 ──────────────────────────────────────────────────────────────
    def _build_request(
        self,
        learner_id: str,
        unit: ReviewMemoryUnit,
        topic: str,
        available_minutes: int,
    ) -> Any:
        # 延迟导入：``personalized_review_card`` 反向依赖 ``services.review``，
        # 模块级导入会把服务层与用例层的加载顺序耦合起来。
        from competition_app.application.personalized_review_card import (
            ReviewCardRequest,
        )

        return ReviewCardRequest(
            learner_id=learner_id,
            user_request=f"请为以下已到期知识点生成一张可立即学习的复习卡：{topic}",
            available_minutes=available_minutes,
            system_operation="due_review_dispatch",
            user_knowledge_state=[
                {
                    "user_id": learner_id,
                    "kp_id": unit.kp_id,
                    "knowledge_mastery": float((unit.mastery_score or 0.0) / 100),
                    "answer_accuracy": float((unit.mastery_score or 0.0) / 100),
                    "forgetting_coefficient": float(unit.lambda_per_day or 0.08),
                    "kp_review_status": "到期",
                    "calculated_at": (
                        unit.source_calculated_at
                        or unit.last_review_at
                        or unit.created_at
                    ),
                }
            ],
        )

    def _dispatch_topic(
        self,
        entry: ReviewQueueEntry,
        kp_names: dict[str, str],
    ) -> str:
        unit = entry.memory_unit
        topic = str(unit.prompt_abstract or "").strip()
        for suffix in _TOPIC_SUFFIXES:
            if topic.endswith(suffix):
                topic = topic[: -len(suffix)].strip()
        if topic in _PLACEHOLDER_TOPICS:
            topic = ""
        if not topic:
            topic = kp_names.get(str(unit.kp_id), "")
        if not topic:
            # 部分知识点的 kp_id 本身就是中文考点名（如教材导入的行），可作兜底。
            raw_kp_id = str(unit.kp_id).strip()
            if sum("\u4e00" <= char <= "\u9fff" for char in raw_kp_id) >= 2:
                topic = raw_kp_id
        return topic

    async def _load_kp_names(self, learner_id: str) -> dict[str, str]:
        if self._kp_name_loader is None:
            return {}
        try:
            return await asyncio.to_thread(self._kp_name_loader, learner_id)
        except Exception as exc:
            logger.warning(
                "到期复习派发解析知识点名称失败 learner=%s: %s", learner_id, exc
            )
            return {}

    def _cooldown_remaining(
        self, learner_id: str, kp_id: str
    ) -> timedelta | None:
        with self._guard:
            failed_at = self._failures.get((learner_id, kp_id))
        if failed_at is None:
            return None
        remaining = self._failure_cooldown - (self._clock() - failed_at)
        return remaining if remaining > timedelta(0) else None

    def _record_failure(self, learner_id: str, kp_id: str) -> None:
        with self._guard:
            self._failures[(learner_id, kp_id)] = self._clock()

    def _clear_failure(self, learner_id: str, kp_id: str) -> None:
        with self._guard:
            self._failures.pop((learner_id, kp_id), None)

    def _release(self, learner_id: str) -> None:
        with self._guard:
            self._running.discard(learner_id)

    def _update_state(self, learner_id: str, patch: dict[str, Any]) -> None:
        with self._guard:
            self._states.setdefault(learner_id, {}).update(patch)

    def _finish(self, learner_id: str, result: dict[str, Any]) -> None:
        summary = {
            "materialized_count": len(result.get("materialized") or []),
            "skipped_count": len(result.get("skipped") or []),
            "remaining": result.get("remaining"),
        }
        self._update_state(
            learner_id,
            {
                "status": result.get("status"),
                "reason": result.get("reason"),
                "summary": summary,
                "finished_at": self._clock().isoformat(),
            },
        )
