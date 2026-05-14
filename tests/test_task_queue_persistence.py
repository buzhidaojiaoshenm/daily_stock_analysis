# -*- coding: utf-8 -*-
"""Regression tests for durable analysis task queue state."""

from __future__ import annotations

from concurrent.futures import Future
from datetime import datetime, timedelta
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.services.task_queue import (
    AnalysisTaskQueue,
    QueueCapacityError,
    TaskFailureType,
    TaskStatus,
)
from src.storage import DatabaseManager


class ExecutorStub:
    def submit(self, *args, **kwargs):
        return Future()


class TaskQueuePersistenceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._original_queue_instance = AnalysisTaskQueue._instance
        AnalysisTaskQueue._instance = None
        DatabaseManager.reset_instance()
        self._temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(self._temp_dir.name) / "tasks.db"
        self.db = DatabaseManager(db_url=f"sqlite:///{db_path}")

    def tearDown(self) -> None:
        queue = AnalysisTaskQueue._instance
        if queue is not None and queue is not self._original_queue_instance:
            executor = getattr(queue, "_executor", None)
            if executor is not None and hasattr(executor, "shutdown"):
                executor.shutdown(wait=False, cancel_futures=True)
        AnalysisTaskQueue._instance = self._original_queue_instance
        DatabaseManager.reset_instance()
        self._temp_dir.cleanup()

    def test_submit_task_persists_pending_state(self) -> None:
        with patch("src.storage.DatabaseManager.get_instance", return_value=self.db):
            queue = AnalysisTaskQueue(max_workers=1, persist_tasks=True)
            queue._executor = ExecutorStub()
            accepted, duplicates = queue.submit_tasks_batch(
                ["600519"],
                stock_name="贵州茅台",
                original_query="茅台",
                selection_source="autocomplete",
                report_type="detailed",
            )

            persisted = self.db.get_analysis_task(accepted[0].task_id)

        self.assertEqual(duplicates, [])
        self.assertIsNotNone(persisted)
        self.assertEqual(persisted.task_id, accepted[0].task_id)
        self.assertEqual(persisted.stock_code, "600519")
        self.assertEqual(persisted.stock_name, "贵州茅台")
        self.assertEqual(persisted.status, TaskStatus.PENDING.value)
        self.assertEqual(persisted.original_query, "茅台")
        self.assertEqual(persisted.selection_source, "autocomplete")

    def test_restart_marks_persisted_inflight_task_failed_instead_of_losing_it(self) -> None:
        with patch("src.storage.DatabaseManager.get_instance", return_value=self.db):
            first_queue = AnalysisTaskQueue(max_workers=1, persist_tasks=True)
            first_queue._executor = ExecutorStub()
            accepted, _ = first_queue.submit_tasks_batch(["600519"], report_type="detailed")
            task_id = accepted[0].task_id

            AnalysisTaskQueue._instance = None
            restarted_queue = AnalysisTaskQueue(max_workers=1, persist_tasks=True)
            restored = restarted_queue.get_task(task_id)
            tasks = restarted_queue.list_all_tasks(limit=10)

        self.assertIsNotNone(restored)
        self.assertEqual(restored.status, TaskStatus.FAILED)
        self.assertIn("服务重启", restored.error)
        self.assertIn(task_id, [task.task_id for task in tasks])

    def test_task_list_keeps_interrupted_tasks_after_new_submission(self) -> None:
        with patch("src.storage.DatabaseManager.get_instance", return_value=self.db):
            first_queue = AnalysisTaskQueue(max_workers=1, persist_tasks=True)
            first_queue._executor = ExecutorStub()
            interrupted, _ = first_queue.submit_tasks_batch(["600519"], report_type="detailed")
            interrupted_task_id = interrupted[0].task_id

            AnalysisTaskQueue._instance = None
            restarted_queue = AnalysisTaskQueue(max_workers=1, persist_tasks=True)
            restarted_queue._executor = ExecutorStub()
            fresh, _ = restarted_queue.submit_tasks_batch(["000001"], report_type="detailed")

            task_ids = [task.task_id for task in restarted_queue.list_all_tasks(limit=10)]

        self.assertIn(interrupted_task_id, task_ids)
        self.assertIn(fresh[0].task_id, task_ids)

    def test_queue_limit_rejects_new_submission_before_over_capacity(self) -> None:
        with patch("src.storage.DatabaseManager.get_instance", return_value=self.db):
            queue = AnalysisTaskQueue(max_workers=1, persist_tasks=True, queue_limit=1)
            queue._executor = ExecutorStub()
            accepted, _ = queue.submit_tasks_batch(["600519"], report_type="detailed")

            with self.assertRaises(QueueCapacityError) as ctx:
                queue.submit_tasks_batch(["000001"], report_type="detailed")

            persisted = self.db.get_analysis_task(accepted[0].task_id)

        self.assertEqual(ctx.exception.limit, 1)
        self.assertEqual(persisted.status, TaskStatus.PENDING.value)

    def test_cancel_task_marks_pending_task_cancelled_and_releases_duplicate_key(self) -> None:
        with patch("src.storage.DatabaseManager.get_instance", return_value=self.db):
            queue = AnalysisTaskQueue(max_workers=1, persist_tasks=True)
            queue._executor = ExecutorStub()
            accepted, _ = queue.submit_tasks_batch(["600519"], report_type="detailed")
            task_id = accepted[0].task_id

            cancelled = queue.cancel_task(task_id, reason="用户取消")
            fresh, duplicates = queue.submit_tasks_batch(["600519"], report_type="detailed")
            persisted = self.db.get_analysis_task(task_id)

        self.assertIsNotNone(cancelled)
        self.assertEqual(cancelled.status, TaskStatus.CANCELLED)
        self.assertEqual(cancelled.failure_type, TaskFailureType.CANCELLED.value)
        self.assertEqual(duplicates, [])
        self.assertEqual(fresh[0].stock_code, "600519")
        self.assertEqual(persisted.status, TaskStatus.CANCELLED.value)
        self.assertEqual(persisted.failure_type, TaskFailureType.CANCELLED.value)

    def test_timeout_scan_marks_stale_processing_task_timeout(self) -> None:
        with patch("src.storage.DatabaseManager.get_instance", return_value=self.db):
            queue = AnalysisTaskQueue(max_workers=1, persist_tasks=True, task_timeout_seconds=30)
            queue._executor = ExecutorStub()
            accepted, _ = queue.submit_tasks_batch(["600519"], report_type="detailed")
            task_id = accepted[0].task_id
            with queue._data_lock:
                task = queue._tasks[task_id]
                task.status = TaskStatus.PROCESSING
                task.started_at = datetime.now() - timedelta(seconds=31)

            timed_out = queue.mark_timed_out_tasks()
            restored = queue.get_task(task_id)
            persisted = self.db.get_analysis_task(task_id)

        self.assertEqual(timed_out, 1)
        self.assertEqual(restored.status, TaskStatus.TIMEOUT)
        self.assertEqual(restored.failure_type, TaskFailureType.TIMEOUT.value)
        self.assertEqual(persisted.status, TaskStatus.TIMEOUT.value)
        self.assertEqual(persisted.failure_type, TaskFailureType.TIMEOUT.value)

    def test_failed_task_persists_retry_count_and_failure_type(self) -> None:
        with patch("src.storage.DatabaseManager.get_instance", return_value=self.db), \
             patch("src.services.analysis_service.AnalysisService") as service_cls:
            service = service_cls.return_value
            service.analyze_stock.return_value = None
            service.last_error = "LLM provider returned invalid JSON"

            queue = AnalysisTaskQueue(max_workers=1, persist_tasks=True)
            task = queue.submit_task("600519", report_type="detailed")
            future = queue._futures[task.task_id]
            future.result(timeout=5)
            restored = queue.get_task(task.task_id)
            persisted = self.db.get_analysis_task(task.task_id)

        self.assertEqual(restored.status, TaskStatus.FAILED)
        self.assertEqual(restored.failure_type, TaskFailureType.LLM.value)
        self.assertEqual(restored.retry_count, 0)
        self.assertEqual(persisted.failure_type, TaskFailureType.LLM.value)

    def test_retry_keeps_task_in_queue_until_retry_budget_exhausted(self) -> None:
        with patch("src.storage.DatabaseManager.get_instance", return_value=self.db), \
             patch("src.services.analysis_service.AnalysisService") as service_cls:
            service = service_cls.return_value
            service.analyze_stock.side_effect = [
                None,
                {
                    "stock_code": "600519",
                    "stock_name": "贵州茅台",
                    "report": {},
                },
            ]
            service.last_error = "data source temporarily unavailable"

            queue = AnalysisTaskQueue(max_workers=1, persist_tasks=True, default_max_retries=1)
            task = queue.submit_task("600519", report_type="detailed")
            first_future = queue._futures[task.task_id]
            first_future.result(timeout=5)
            retry_future = queue._futures[task.task_id]
            retry_future.result(timeout=5)
            restored = queue.get_task(task.task_id)
            persisted = self.db.get_analysis_task(task.task_id)

        self.assertEqual(restored.status, TaskStatus.COMPLETED)
        self.assertEqual(restored.retry_count, 1)
        self.assertEqual(persisted.retry_count, 1)
