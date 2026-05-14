# -*- coding: utf-8 -*-
"""
===================================
A股自选股智能分析系统 - 异步任务队列
===================================

职责：
1. 管理异步分析任务的生命周期
2. 防止相同股票代码重复提交
3. 提供 SSE 事件广播机制
4. 任务完成后持久化到数据库
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, Future
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, List, Any, TYPE_CHECKING, Tuple, Literal

if TYPE_CHECKING:
    from asyncio import Queue as AsyncQueue

from data_provider.base import canonical_stock_code, normalize_stock_code
from src.utils.analysis_metadata import SELECTION_SOURCES

logger = logging.getLogger(__name__)


def _dedupe_stock_code_key(stock_code: str) -> str:
    """
    Build the internal duplicate-detection key for a stock code.

    The task queue should treat equivalent market code shapes as the same
    underlying stock, e.g. ``600519`` and ``600519.SH``.
    """
    return canonical_stock_code(normalize_stock_code(stock_code))


class TaskStatus(str, Enum):
    """Task status enumeration"""
    PENDING = "pending"        # Waiting for execution
    PROCESSING = "processing"  # In progress
    COMPLETED = "completed"    # Completed
    FAILED = "failed"          # Failed
    CANCELLED = "cancelled"    # Cancelled before completion
    TIMEOUT = "timeout"        # Timed out before completion


class TaskFailureType(str, Enum):
    """Machine-readable terminal reason for non-completed tasks."""
    VALIDATION = "validation"
    DATA_SOURCE = "data_source"
    LLM = "llm"
    NOTIFICATION = "notification"
    TIMEOUT = "timeout"
    INTERNAL = "internal"
    CANCELLED = "cancelled"


@dataclass
class TaskInfo:
    """
    Task information dataclass.

    Used for API responses and internal task management.
    """
    task_id: str
    stock_code: str
    stock_name: Optional[str] = None
    status: TaskStatus = TaskStatus.PENDING
    progress: int = 0
    message: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    failure_type: Optional[str] = None
    report_type: str = "detailed"
    retry_count: int = 0
    max_retries: int = 0
    created_at: datetime = field(default_factory=datetime.now)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    original_query: Optional[str] = None
    selection_source: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert task info into an API-friendly dictionary."""
        return {
            "task_id": self.task_id,
            "stock_code": self.stock_code,
            "stock_name": self.stock_name,
            "status": self.status.value,
            "progress": self.progress,
            "message": self.message,
            "report_type": self.report_type,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "error": self.error,
            "failure_type": self.failure_type,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "original_query": self.original_query,
            "selection_source": self.selection_source,
        }
    
    def copy(self) -> 'TaskInfo':
        """Create a shallow copy of the task information."""
        return TaskInfo(
            task_id=self.task_id,
            stock_code=self.stock_code,
            stock_name=self.stock_name,
            status=self.status,
            progress=self.progress,
            message=self.message,
            result=self.result,
            error=self.error,
            failure_type=self.failure_type,
            report_type=self.report_type,
            retry_count=self.retry_count,
            max_retries=self.max_retries,
            created_at=self.created_at,
            started_at=self.started_at,
            completed_at=self.completed_at,
            original_query=self.original_query,
            selection_source=self.selection_source,
        )


class DuplicateTaskError(Exception):
    """
    重复提交异常
    
    当股票已在分析中时抛出此异常
    """
    def __init__(self, stock_code: str, existing_task_id: str):
        self.stock_code = stock_code
        self.existing_task_id = existing_task_id
        super().__init__(f"股票 {stock_code} 正在分析中 (task_id: {existing_task_id})")


class QueueCapacityError(Exception):
    """Raised when accepting a new task would exceed the configured queue limit."""

    def __init__(self, limit: int, current: int, requested: int):
        self.limit = limit
        self.current = current
        self.requested = requested
        super().__init__(
            f"分析任务队列已满: limit={limit}, current={current}, requested={requested}"
        )


class AnalysisTaskQueue:
    """
    异步分析任务队列
    
    单例模式，全局唯一实例
    
    特性：
    1. 防止相同股票代码重复提交
    2. 线程池执行分析任务
    3. SSE 事件广播机制
    4. 任务完成后自动持久化
    """
    
    _instance: Optional['AnalysisTaskQueue'] = None
    _instance_lock = threading.Lock()
    
    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(
        self,
        max_workers: int = 3,
        persist_tasks: bool = False,
        *,
        task_timeout_seconds: int = 0,
        queue_limit: int = 0,
        default_max_retries: int = 0,
    ):
        # 防止重复初始化
        if hasattr(self, '_initialized') and self._initialized:
            self.sync_runtime_limits(
                task_timeout_seconds=task_timeout_seconds,
                queue_limit=queue_limit,
                default_max_retries=default_max_retries,
            )
            if persist_tasks:
                self.enable_persistence()
            return
        
        self._max_workers = max_workers
        self._executor: Optional[ThreadPoolExecutor] = None
        
        # 核心数据结构
        self._tasks: Dict[str, TaskInfo] = {}           # task_id -> TaskInfo
        self._analyzing_stocks: Dict[str, str] = {}     # dedupe_key -> task_id
        self._futures: Dict[str, Future] = {}           # task_id -> Future
        
        # SSE 订阅者列表（asyncio.Queue 实例）
        self._subscribers: List['AsyncQueue'] = []
        self._subscribers_lock = threading.Lock()
        
        # 主事件循环引用（用于跨线程广播）
        self._main_loop: Optional[asyncio.AbstractEventLoop] = None
        
        # 线程安全锁
        self._data_lock = threading.RLock()
        
        # 任务历史保留数量（内存中）
        self._max_history = 100
        self._persist_tasks = False
        self._task_timeout_seconds = max(0, int(task_timeout_seconds or 0))
        self._queue_limit = max(0, int(queue_limit or 0))
        self._default_max_retries = max(0, int(default_max_retries or 0))
        
        self._initialized = True
        if persist_tasks:
            self.enable_persistence()
        logger.info(f"[TaskQueue] 初始化完成，最大并发: {max_workers}")
    
    @property
    def executor(self) -> ThreadPoolExecutor:
        """懒加载线程池"""
        if self._executor is None:
            self._executor = ThreadPoolExecutor(
                max_workers=self._max_workers,
                thread_name_prefix="analysis_task_"
            )
        return self._executor

    @property
    def max_workers(self) -> int:
        """Return current executor max worker setting."""
        return self._max_workers

    def _has_inflight_tasks_locked(self) -> bool:
        """Check whether queue has any pending/processing tasks."""
        if self._analyzing_stocks:
            return True
        return any(
            task.status in (TaskStatus.PENDING, TaskStatus.PROCESSING)
            for task in self._tasks.values()
        )

    def sync_max_workers(
        self,
        max_workers: int,
        *,
        log: bool = True,
    ) -> Literal["applied", "unchanged", "deferred_busy"]:
        """
        Try to sync queue concurrency without replacing singleton instance.

        Returns:
            - "applied": new value applied immediately (idle queue only)
            - "unchanged": target equals current value or invalid target
            - "deferred_busy": queue is busy, apply is deferred
        """
        try:
            target = max(1, int(max_workers))
        except (TypeError, ValueError):
            if log:
                logger.warning("[TaskQueue] 忽略非法 MAX_WORKERS 值: %r", max_workers)
            return "unchanged"

        executor_to_shutdown: Optional[ThreadPoolExecutor] = None
        previous: int
        with self._data_lock:
            previous = self._max_workers
            if target == previous:
                return "unchanged"

            if self._has_inflight_tasks_locked():
                if log:
                    logger.info(
                        "[TaskQueue] 最大并发调整延后: 当前繁忙 (%s -> %s)",
                        previous,
                        target,
                    )
                return "deferred_busy"

            self._max_workers = target
            executor_to_shutdown = self._executor
            self._executor = None

        if executor_to_shutdown is not None:
            executor_to_shutdown.shutdown(wait=False)

        if log:
            logger.info("[TaskQueue] 最大并发已更新: %s -> %s", previous, target)
        return "applied"

    def sync_runtime_limits(
        self,
        *,
        task_timeout_seconds: Optional[int] = None,
        queue_limit: Optional[int] = None,
        default_max_retries: Optional[int] = None,
    ) -> None:
        """Update runtime task-queue limits without replacing the singleton."""
        with self._data_lock:
            if task_timeout_seconds is not None:
                self._task_timeout_seconds = max(0, int(task_timeout_seconds or 0))
            if queue_limit is not None:
                self._queue_limit = max(0, int(queue_limit or 0))
            if default_max_retries is not None:
                self._default_max_retries = max(0, int(default_max_retries or 0))

    def enable_persistence(self) -> None:
        """Enable durable DB-backed task snapshots for API/runtime queues."""
        with self._data_lock:
            if self._persist_tasks:
                return
            self._persist_tasks = True
            has_memory_tasks = bool(self._tasks)

        if has_memory_tasks:
            self._persist_current_memory_tasks()
            return

        try:
            db = self._get_task_db()
            interrupted = db.mark_incomplete_analysis_tasks_failed(
                "服务重启，任务已中断，请重新提交分析"
            )
            if interrupted:
                logger.warning("[TaskQueue] 标记 %s 个重启中断任务为失败", interrupted)
        except Exception as exc:
            logger.debug("[TaskQueue] 启用任务持久化失败（降级为内存队列）: %s", exc)

    def _get_task_db(self):
        from src.storage import DatabaseManager

        return DatabaseManager.get_instance()

    def _persist_current_memory_tasks(self) -> None:
        with self._data_lock:
            snapshots = [task.copy() for task in self._tasks.values()]
        for task in snapshots:
            self._persist_task_snapshot(task)

    def _persist_task_snapshot(self, task: Optional[TaskInfo]) -> None:
        if not self._persist_tasks or task is None:
            return

        try:
            self._get_task_db().upsert_analysis_task({
                "task_id": task.task_id,
                "stock_code": task.stock_code,
                "stock_name": task.stock_name,
                "status": task.status.value,
                "progress": task.progress,
                "message": task.message,
                "result": task.result,
                "error": task.error,
                "failure_type": task.failure_type,
                "report_type": task.report_type,
                "retry_count": task.retry_count,
                "max_retries": task.max_retries,
                "created_at": task.created_at,
                "started_at": task.started_at,
                "completed_at": task.completed_at,
                "original_query": task.original_query,
                "selection_source": task.selection_source,
            })
        except Exception as exc:
            logger.debug(
                "[TaskQueue] 任务持久化失败（fail-open）: task_id=%s err=%s",
                task.task_id,
                exc,
            )

    def _task_from_persisted_row(self, row: Any) -> TaskInfo:
        result = None
        result_json = getattr(row, "result_json", None)
        if result_json:
            try:
                parsed = json.loads(result_json)
                result = parsed if isinstance(parsed, dict) else None
            except Exception:
                result = None

        status_value = getattr(row, "status", TaskStatus.PENDING.value)
        try:
            status = TaskStatus(status_value)
        except ValueError:
            status = TaskStatus.FAILED

        return TaskInfo(
            task_id=row.task_id,
            stock_code=row.stock_code,
            stock_name=getattr(row, "stock_name", None),
            status=status,
            progress=int(getattr(row, "progress", 0) or 0),
            message=getattr(row, "message", None),
            result=result,
            error=getattr(row, "error", None),
            failure_type=getattr(row, "failure_type", None),
            report_type=getattr(row, "report_type", None) or "detailed",
            retry_count=int(getattr(row, "retry_count", 0) or 0),
            max_retries=int(getattr(row, "max_retries", 0) or 0),
            created_at=getattr(row, "created_at", None) or datetime.now(),
            started_at=getattr(row, "started_at", None),
            completed_at=getattr(row, "completed_at", None),
            original_query=getattr(row, "original_query", None),
            selection_source=getattr(row, "selection_source", None),
        )
    
    # ========== 任务提交与查询 ==========
    
    def is_analyzing(self, stock_code: str) -> bool:
        """
        检查股票是否正在分析中
        
        Args:
            stock_code: 股票代码
            
        Returns:
            True 表示正在分析中
        """
        dedupe_key = _dedupe_stock_code_key(stock_code)
        with self._data_lock:
            return dedupe_key in self._analyzing_stocks
    
    def get_analyzing_task_id(self, stock_code: str) -> Optional[str]:
        """
        获取正在分析该股票的任务 ID
        
        Args:
            stock_code: 股票代码
            
        Returns:
            任务 ID，如果没有则返回 None
        """
        dedupe_key = _dedupe_stock_code_key(stock_code)
        with self._data_lock:
            return self._analyzing_stocks.get(dedupe_key)

    def validate_selection_source(self, selection_source: Optional[str]) -> None:
        """
        Validate the selection source parameter.

        Args:
            selection_source: Selection source label.

        Raises:
            ValueError: Raised when the selection source is invalid.
        """
        if selection_source is not None and selection_source not in SELECTION_SOURCES:
            raise ValueError(
                f"Invalid selection_source: {selection_source}. "
                f"Must be one of {SELECTION_SOURCES}"
            )
    
    def submit_task(
        self,
        stock_code: str,
        stock_name: Optional[str] = None,
        original_query: Optional[str] = None,
        selection_source: Optional[str] = None,
        report_type: str = "detailed",
        force_refresh: bool = False,
        notify: bool = True,
        max_retries: Optional[int] = None,
    ) -> TaskInfo:
        """
        Submit a single analysis task.

        Args:
            stock_code: Stock code
            stock_name: Optional stock name
            original_query: Optional raw user input
            selection_source: Optional source label
            report_type: Report type
            force_refresh: Whether to bypass cache

        Returns:
            TaskInfo: Accepted task information

        Raises:
            DuplicateTaskError: Raised when the stock is already being analyzed
        """
        stock_code = canonical_stock_code(stock_code)
        if not stock_code:
            raise ValueError("股票代码不能为空或仅包含空白字符")

        accepted, duplicates = self.submit_tasks_batch(
            [stock_code],
            stock_name=stock_name,
            original_query=original_query,
            selection_source=selection_source,
            report_type=report_type,
            force_refresh=force_refresh,
            notify=notify,
            max_retries=max_retries,
        )
        if duplicates:
            raise duplicates[0]
        return accepted[0]

    def submit_tasks_batch(
        self,
        stock_codes: List[str],
        stock_name: Optional[str] = None,
        original_query: Optional[str] = None,
        selection_source: Optional[str] = None,
        report_type: str = "detailed",
        force_refresh: bool = False,
        notify: bool = True,
        max_retries: Optional[int] = None,
    ) -> Tuple[List[TaskInfo], List[DuplicateTaskError]]:
        """
        Submit analysis tasks in batch.

        - Duplicate stocks are skipped and recorded in duplicates.
        - If executor submission fails, the current batch is rolled back.
        """
        self.validate_selection_source(selection_source)

        accepted: List[TaskInfo] = []
        duplicates: List[DuplicateTaskError] = []
        created_task_ids: List[str] = []

        canonical_codes = [
            normalized for normalized in (canonical_stock_code(code) for code in stock_codes)
            if normalized
        ]
        task_max_retries = (
            max(0, int(max_retries))
            if max_retries is not None
            else self._default_max_retries
        )
        self.mark_timed_out_tasks()

        with self._data_lock:
            self._raise_if_batch_exceeds_capacity_locked(canonical_codes)

            for stock_code in canonical_codes:
                dedupe_key = _dedupe_stock_code_key(stock_code)
                if dedupe_key in self._analyzing_stocks:
                    existing_task_id = self._analyzing_stocks[dedupe_key]
                    duplicates.append(DuplicateTaskError(stock_code, existing_task_id))
                    continue

                task_id = uuid.uuid4().hex
                task_info = TaskInfo(
                    task_id=task_id,
                    stock_code=stock_code,
                    stock_name=stock_name,
                    status=TaskStatus.PENDING,
                    message="任务已加入队列",
                    report_type=report_type,
                    max_retries=task_max_retries,
                    original_query=original_query,
                    selection_source=selection_source,
                )
                self._tasks[task_id] = task_info
                self._analyzing_stocks[dedupe_key] = task_id

                try:
                    future = self.executor.submit(
                        self._execute_task,
                        task_id,
                        stock_code,
                        report_type,
                        force_refresh,
                        notify,
                    )
                except Exception:
                    # Roll back the current batch to avoid partial submission.
                    self._rollback_submitted_tasks_locked(created_task_ids + [task_id])
                    raise

                self._futures[task_id] = future
                accepted.append(task_info)
                created_task_ids.append(task_id)
                logger.info(f"[TaskQueue] 任务已提交: {stock_code} -> {task_id}")

            # Keep task_created ordered before worker-emitted task_started/task_completed.
            # Broadcasting here also preserves batch rollback semantics because we only
            # reach this point after every submit in the batch has succeeded.
            for task_info in accepted:
                self._persist_task_snapshot(task_info)
                self._broadcast_event("task_created", task_info.to_dict())

        return accepted, duplicates

    def _active_task_count_locked(self) -> int:
        return sum(
            1
            for task in self._tasks.values()
            if task.status in (TaskStatus.PENDING, TaskStatus.PROCESSING)
        )

    def _raise_if_batch_exceeds_capacity_locked(self, canonical_codes: List[str]) -> None:
        if self._queue_limit <= 0:
            return

        requested = 0
        seen: set[str] = set()
        for stock_code in canonical_codes:
            dedupe_key = _dedupe_stock_code_key(stock_code)
            if dedupe_key in seen or dedupe_key in self._analyzing_stocks:
                continue
            seen.add(dedupe_key)
            requested += 1

        current = self._active_task_count_locked()
        if requested and current + requested > self._queue_limit:
            raise QueueCapacityError(self._queue_limit, current, requested)

    def _rollback_submitted_tasks_locked(self, task_ids: List[str]) -> None:
        """回滚当前批次已创建但尚未稳定返回给调用方的任务。"""
        for task_id in task_ids:
            future = self._futures.pop(task_id, None)
            if future is not None:
                future.cancel()

            task = self._tasks.pop(task_id, None)
            if task:
                dedupe_key = _dedupe_stock_code_key(task.stock_code)
                if self._analyzing_stocks.get(dedupe_key) == task_id:
                    del self._analyzing_stocks[dedupe_key]
    
    def get_task(self, task_id: str) -> Optional[TaskInfo]:
        """
        获取任务信息
        
        Args:
            task_id: 任务 ID
            
        Returns:
            TaskInfo 或 None
        """
        self.mark_timed_out_tasks()
        with self._data_lock:
            task = self._tasks.get(task_id)
            if task:
                return task.copy()

        if self._persist_tasks:
            try:
                row = self._get_task_db().get_analysis_task(task_id)
                return self._task_from_persisted_row(row) if row else None
            except Exception as exc:
                logger.debug("[TaskQueue] 读取持久化任务失败: task_id=%s err=%s", task_id, exc)
                return None

        return None
    
    def list_pending_tasks(self) -> List[TaskInfo]:
        """
        获取所有进行中的任务（pending + processing）
        
        Returns:
            任务列表（副本）
        """
        self.mark_timed_out_tasks()
        with self._data_lock:
            memory_tasks = [
                task.copy() for task in self._tasks.values()
                if task.status in (TaskStatus.PENDING, TaskStatus.PROCESSING)
            ]
        if not self._persist_tasks:
            return memory_tasks

        try:
            rows = self._get_task_db().list_analysis_tasks(
                limit=self._max_history,
                statuses=[TaskStatus.PENDING.value, TaskStatus.PROCESSING.value],
            )
            return [self._task_from_persisted_row(row) for row in rows]
        except Exception as exc:
            logger.debug("[TaskQueue] 读取持久化进行中任务失败: %s", exc)
            return memory_tasks
    
    def list_all_tasks(self, limit: int = 50) -> List[TaskInfo]:
        """
        获取所有任务（按创建时间倒序）
        
        Args:
            limit: 返回数量限制
            
        Returns:
            任务列表（副本）
        """
        self.mark_timed_out_tasks()
        with self._data_lock:
            memory_tasks = sorted(
                self._tasks.values(),
                key=lambda t: t.created_at,
                reverse=True
            )
            if not self._persist_tasks:
                return [t.copy() for t in memory_tasks[:limit]]

        try:
            rows = self._get_task_db().list_analysis_tasks(limit=limit)
            return [self._task_from_persisted_row(row) for row in rows]
        except Exception as exc:
            logger.debug("[TaskQueue] 读取持久化任务列表失败: %s", exc)
            return [t.copy() for t in memory_tasks[:limit]]
    
    def get_task_stats(self) -> Dict[str, int]:
        """
        获取任务统计信息
        
        Returns:
            统计信息字典
        """
        self.mark_timed_out_tasks()
        with self._data_lock:
            if self._persist_tasks:
                try:
                    return self._get_task_db().get_analysis_task_stats()
                except Exception as exc:
                    logger.debug("[TaskQueue] 读取持久化任务统计失败: %s", exc)

            stats = {
                "total": len(self._tasks),
                "pending": 0,
                "processing": 0,
                "completed": 0,
                "failed": 0,
                "cancelled": 0,
                "timeout": 0,
            }
            for task in self._tasks.values():
                stats[task.status.value] = stats.get(task.status.value, 0) + 1
            return stats

    def update_task_progress(
        self,
        task_id: str,
        progress: int,
        message: Optional[str] = None,
        *,
        event_type: str = "task_progress",
    ) -> Optional[TaskInfo]:
        """
        Update in-flight task progress and broadcast an SSE event.

        Only pending/processing tasks are updated. Progress is clamped to
        [0, 99] so terminal states remain controlled by completion/failure.
        """
        with self._data_lock:
            task = self._tasks.get(task_id)
            if not task or task.status not in (TaskStatus.PENDING, TaskStatus.PROCESSING):
                return None

            next_progress = max(task.progress, max(0, min(99, int(progress))))
            changed = False
            if next_progress != task.progress:
                task.progress = next_progress
                changed = True
            if message is not None and message != task.message:
                task.message = message
                changed = True

            if not changed:
                return task.copy()

            task_snapshot = task.copy()

        self._persist_task_snapshot(task_snapshot)
        self._broadcast_event(event_type, task_snapshot.to_dict())
        return task_snapshot

    def cancel_task(self, task_id: str, reason: Optional[str] = None) -> Optional[TaskInfo]:
        """Cooperatively cancel a pending or processing task."""
        message = reason or "任务已取消"
        with self._data_lock:
            task = self._tasks.get(task_id)
            if not task or task.status not in (TaskStatus.PENDING, TaskStatus.PROCESSING):
                return None
            future = self._futures.get(task_id)
            if future is not None:
                future.cancel()
            self._mark_task_terminal_locked(
                task,
                status=TaskStatus.CANCELLED,
                message=message,
                error=message,
                failure_type=TaskFailureType.CANCELLED.value,
            )
            task_snapshot = task.copy()

        self._persist_task_snapshot(task_snapshot)
        self._broadcast_event("task_cancelled", task_snapshot.to_dict())
        return task_snapshot

    def mark_timed_out_tasks(self) -> int:
        """Mark stale pending/processing tasks as timed out."""
        with self._data_lock:
            snapshots = self._mark_timed_out_tasks_locked()

        for snapshot in snapshots:
            self._persist_task_snapshot(snapshot)
            self._broadcast_event("task_timeout", snapshot.to_dict())
        return len(snapshots)

    def _mark_timed_out_tasks_locked(self) -> List[TaskInfo]:
        if self._task_timeout_seconds <= 0:
            return []

        now = datetime.now()
        snapshots: List[TaskInfo] = []
        for task in self._tasks.values():
            if task.status not in (TaskStatus.PENDING, TaskStatus.PROCESSING):
                continue
            reference = task.started_at or task.created_at
            if (now - reference).total_seconds() < self._task_timeout_seconds:
                continue
            self._mark_task_terminal_locked(
                task,
                status=TaskStatus.TIMEOUT,
                message=f"任务超过 {self._task_timeout_seconds} 秒未完成，已标记为超时",
                error=f"任务超过 {self._task_timeout_seconds} 秒未完成",
                failure_type=TaskFailureType.TIMEOUT.value,
            )
            future = self._futures.get(task.task_id)
            if future is not None:
                future.cancel()
            snapshots.append(task.copy())
        return snapshots

    def _mark_task_terminal_locked(
        self,
        task: TaskInfo,
        *,
        status: TaskStatus,
        message: str,
        error: Optional[str] = None,
        failure_type: Optional[str] = None,
        result: Optional[Dict[str, Any]] = None,
    ) -> None:
        task.status = status
        task.completed_at = datetime.now()
        task.message = message
        task.error = error
        task.failure_type = failure_type
        if result is not None:
            task.result = result
        if status == TaskStatus.COMPLETED:
            task.progress = 100
        dedupe_key = _dedupe_stock_code_key(task.stock_code)
        if self._analyzing_stocks.get(dedupe_key) == task.task_id:
            del self._analyzing_stocks[dedupe_key]
    
    # ========== 任务执行 ==========
    
    def _execute_task(
        self,
        task_id: str,
        stock_code: str,
        report_type: str,
        force_refresh: bool,
        notify: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """
        执行分析任务（在线程池中运行）
        
        Args:
            task_id: 任务 ID
            stock_code: 股票代码
            report_type: 报告类型
            force_refresh: 是否强制刷新
            
        Returns:
            分析结果字典
        """
        # 更新状态为处理中
        with self._data_lock:
            task = self._tasks.get(task_id)
            if not task or task.status in (
                TaskStatus.CANCELLED,
                TaskStatus.TIMEOUT,
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
            ):
                return None
            task.status = TaskStatus.PROCESSING
            task.started_at = datetime.now()
            task.message = "正在分析中..."
            task.progress = 10
            task_snapshot = task.copy()
        
        self._persist_task_snapshot(task_snapshot)
        self._broadcast_event("task_started", task_snapshot.to_dict())
        
        try:
            # 导入分析服务（延迟导入避免循环依赖）
            from src.services.analysis_service import AnalysisService
            
            # 执行分析
            service = AnalysisService()

            def _on_progress(progress: int, message: str) -> None:
                self.update_task_progress(task_id, progress, message)

            result = service.analyze_stock(
                stock_code=stock_code,
                report_type=report_type,
                force_refresh=force_refresh,
                query_id=task_id,
                send_notification=notify,
                progress_callback=_on_progress,
            )
            
            if result:
                # 更新任务状态为完成
                with self._data_lock:
                    task = self._tasks.get(task_id)
                    if task and task.status == TaskStatus.PROCESSING:
                        task.result = result
                        task.message = "分析完成"
                        task.stock_name = result.get("stock_name", task.stock_name)
                        task.failure_type = None
                        self._mark_task_terminal_locked(
                            task,
                            status=TaskStatus.COMPLETED,
                            message="分析完成",
                            result=result,
                        )
                        task_snapshot = task.copy()
                    else:
                        task_snapshot = None
                
                self._persist_task_snapshot(task_snapshot)
                if task_snapshot:
                    self._broadcast_event("task_completed", task_snapshot.to_dict())
                logger.info(f"[TaskQueue] 任务完成: {task_id} ({stock_code})")
                
                # 清理过期任务
                self._cleanup_old_tasks()
                
                return result
            else:
                # 分析返回空结果
                raise Exception(service.last_error or "分析返回空结果")
                
        except Exception as e:
            error_msg = str(e)
            failure_type = self._classify_failure(e, error_msg)
            logger.error(f"[TaskQueue] 任务失败: {task_id} ({stock_code}), 错误: {error_msg}")
            
            with self._data_lock:
                task = self._tasks.get(task_id)
                if task and task.status == TaskStatus.PROCESSING:
                    task.error = error_msg[:200]  # 限制错误信息长度
                    task.failure_type = failure_type
                    if task.retry_count < task.max_retries:
                        task.retry_count += 1
                        task.status = TaskStatus.PENDING
                        task.progress = 0
                        task.message = (
                            f"分析失败，准备重试 ({task.retry_count}/{task.max_retries}): "
                            f"{error_msg[:50]}"
                        )
                        task_snapshot = task.copy()
                        try:
                            future = self.executor.submit(
                                self._execute_task,
                                task_id,
                                stock_code,
                                report_type,
                                force_refresh,
                                notify,
                            )
                            self._futures[task_id] = future
                        except Exception as submit_exc:
                            retry_error = f"重试提交失败: {submit_exc}"
                            self._mark_task_terminal_locked(
                                task,
                                status=TaskStatus.FAILED,
                                message=retry_error[:80],
                                error=retry_error[:200],
                                failure_type=TaskFailureType.INTERNAL.value,
                            )
                            task_snapshot = task.copy()
                    else:
                        self._mark_task_terminal_locked(
                            task,
                            status=TaskStatus.FAILED,
                            message=f"分析失败: {error_msg[:50]}",
                            error=error_msg[:200],
                            failure_type=failure_type,
                        )
                        task_snapshot = task.copy()
                else:
                    task_snapshot = None
            
            self._persist_task_snapshot(task_snapshot)
            if task_snapshot:
                event_type = (
                    "task_retrying"
                    if task_snapshot.status == TaskStatus.PENDING
                    else "task_failed"
                )
                self._broadcast_event(event_type, task_snapshot.to_dict())
            
            # 清理过期任务
            self._cleanup_old_tasks()
            
            return None

    @staticmethod
    def _classify_failure(exc: Exception, message: str) -> str:
        """Map an exception/message to a stable task failure category."""
        text = f"{exc.__class__.__name__} {message}".lower()
        if "timeout" in text or "timed out" in text or "超时" in text:
            return TaskFailureType.TIMEOUT.value
        if "llm" in text or "model" in text or "json" in text or "provider" in text:
            return TaskFailureType.LLM.value
        if "validation" in text or "invalid" in text or "校验" in text or "参数" in text:
            return TaskFailureType.VALIDATION.value
        if "notification" in text or "webhook" in text or "telegram" in text or "wechat" in text:
            return TaskFailureType.NOTIFICATION.value
        if "data source" in text or "数据源" in text or "quote" in text or "行情" in text:
            return TaskFailureType.DATA_SOURCE.value
        return TaskFailureType.INTERNAL.value
    
    def _cleanup_old_tasks(self) -> int:
        """
        清理过期的已完成任务
        
        保留最近 _max_history 个任务
        
        Returns:
            清理的任务数量
        """
        with self._data_lock:
            if len(self._tasks) <= self._max_history:
                return 0
            
            # 按时间排序，删除旧的已完成任务
            completed_tasks = sorted(
                [t for t in self._tasks.values()
                 if t.status in (TaskStatus.COMPLETED, TaskStatus.FAILED)],
                key=lambda t: t.created_at
            )
            
            to_remove = len(self._tasks) - self._max_history
            removed = 0
            
            for task in completed_tasks[:to_remove]:
                del self._tasks[task.task_id]
                if task.task_id in self._futures:
                    del self._futures[task.task_id]
                removed += 1
            
            if removed > 0:
                logger.debug(f"[TaskQueue] 清理了 {removed} 个过期任务")
            
            return removed
    
    # ========== SSE 事件广播 ==========
    
    def subscribe(self, queue: 'AsyncQueue') -> None:
        """
        订阅任务事件
        
        Args:
            queue: asyncio.Queue 实例，用于接收事件
        """
        with self._subscribers_lock:
            self._subscribers.append(queue)
            # 捕获当前事件循环（应在主线程的 async 上下文中调用）
            try:
                self._main_loop = asyncio.get_running_loop()
            except RuntimeError:
                # 如果不在 async 上下文中，尝试获取事件循环
                try:
                    self._main_loop = asyncio.get_event_loop()
                except RuntimeError:
                    pass
            logger.debug(f"[TaskQueue] 新订阅者加入，当前订阅者数: {len(self._subscribers)}")
    
    def unsubscribe(self, queue: 'AsyncQueue') -> None:
        """
        取消订阅任务事件
        
        Args:
            queue: 要取消订阅的 asyncio.Queue 实例
        """
        with self._subscribers_lock:
            if queue in self._subscribers:
                self._subscribers.remove(queue)
                logger.debug(f"[TaskQueue] 订阅者离开，当前订阅者数: {len(self._subscribers)}")
    
    def _broadcast_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """
        广播事件到所有订阅者
        
        使用 call_soon_threadsafe 确保跨线程安全
        
        Args:
            event_type: 事件类型
            data: 事件数据
        """
        event = {"type": event_type, "data": data}
        
        with self._subscribers_lock:
            subscribers = self._subscribers.copy()
            loop = self._main_loop
        
        if not subscribers:
            return
        
        if loop is None:
            logger.warning("[TaskQueue] 无法广播事件：主事件循环未设置")
            return
        
        for queue in subscribers:
            try:
                # 使用 call_soon_threadsafe 将事件放入 asyncio 队列
                # 这是从工作线程向主事件循环发送消息的安全方式
                loop.call_soon_threadsafe(queue.put_nowait, event)
            except RuntimeError as e:
                # 事件循环已关闭
                logger.debug(f"[TaskQueue] 广播事件跳过（循环已关闭）: {e}")
            except Exception as e:
                logger.warning(f"[TaskQueue] 广播事件失败: {e}")
    
    # ========== 清理方法 ==========
    
    def shutdown(self) -> None:
        """关闭任务队列"""
        if self._executor:
            self._executor.shutdown(wait=True)
            self._executor = None
            logger.info("[TaskQueue] 线程池已关闭")


# ========== 便捷函数 ==========

def get_task_queue() -> AnalysisTaskQueue:
    """
    获取任务队列单例
    
    Returns:
        AnalysisTaskQueue 实例
    """
    queue = AnalysisTaskQueue(persist_tasks=True)
    try:
        from src.config import get_config

        config = get_config()
        target_workers = max(1, int(getattr(config, "max_workers", queue.max_workers)))
        queue.sync_max_workers(target_workers, log=False)
        queue.sync_runtime_limits(
            task_timeout_seconds=getattr(config, "analysis_task_timeout_seconds", 0),
            queue_limit=getattr(config, "analysis_task_queue_limit", 100),
            default_max_retries=getattr(config, "analysis_task_max_retries", 0),
        )
    except Exception as exc:
        logger.debug("[TaskQueue] 读取 MAX_WORKERS 失败，使用当前并发设置: %s", exc)

    return queue
