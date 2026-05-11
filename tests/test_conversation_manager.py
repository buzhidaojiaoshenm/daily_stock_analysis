# -*- coding: utf-8 -*-
"""Thread-safety regression tests for ConversationManager."""

import threading
import unittest
from unittest.mock import patch

from src.agent.conversation import ConversationManager, compact_conversation_history


class ConversationManagerThreadSafetyTestCase(unittest.TestCase):
    def test_compaction_summarizes_omitted_earlier_messages(self):
        messages = [
            {
                "role": "user",
                "content": "第一轮提问：请记住我关注现金流、订单增长和监管风险。" + ("甲" * 400),
            },
            {
                "role": "assistant",
                "content": "第一轮回答：已记录关注点，并会在后续分析中优先检查。" + ("乙" * 400),
            },
            {"role": "user", "content": "最近问题：继续分析贵州茅台。"},
            {"role": "assistant", "content": "最近回答：重点看估值和业绩。"},
        ]

        compacted = compact_conversation_history(
            messages,
            max_tokens=40,
            per_message_tokens=30,
        )

        self.assertEqual(compacted[0]["role"], "system")
        self.assertIn("Earlier conversation history was compressed", compacted[0]["content"])
        self.assertIn("user: 第一轮提问：请记住我关注现金流", compacted[0]["content"])
        self.assertIn("assistant: 第一轮回答：已记录关注点", compacted[0]["content"])
        self.assertNotIn("甲" * 100, compacted[0]["content"])
        self.assertIn({"role": "user", "content": "最近问题：继续分析贵州茅台。"}, compacted)

    def test_add_message_is_safe_under_parallel_session_creation(self):
        manager = ConversationManager()
        errors = []
        start = threading.Event()

        def _worker(worker_id: int) -> None:
            start.wait()
            try:
                for message_id in range(1000):
                    manager.add_message(f"session-{worker_id}-{message_id}", "user", "hello")
            except Exception as exc:  # pragma: no cover - failures are asserted below
                errors.append(exc)

        threads = [
            threading.Thread(target=_worker, args=(idx,), daemon=True)
            for idx in range(6)
        ]

        with patch("src.agent.conversation.ConversationSession.add_message", autospec=True):
            for thread in threads:
                thread.start()
            start.set()
            for thread in threads:
                thread.join()

        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
