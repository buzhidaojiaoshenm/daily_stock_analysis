# -*- coding: utf-8 -*-
"""Regression tests for durable analysis task queue state."""

from __future__ import annotations

from concurrent.futures import Future
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.services.task_queue import AnalysisTaskQueue, TaskStatus
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
