# -*- coding: utf-8 -*-
"""
Conversation Manager for Agent multi-turn chat.

Manages conversation sessions with TTL, storing message history and context.
"""

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from src.storage import get_db

logger = logging.getLogger(__name__)

_APPROX_CHARS_PER_TOKEN = 4
DEFAULT_HISTORY_TOKEN_BUDGET = 800
DEFAULT_HISTORY_MESSAGE_TOKEN_BUDGET = 500
_OMITTED_SUMMARY_MAX_MESSAGES = 4
_OMITTED_SUMMARY_SNIPPET_CHARS = 96


def _estimate_tokens(text: str) -> int:
    return max(1, (len(text) + _APPROX_CHARS_PER_TOKEN - 1) // _APPROX_CHARS_PER_TOKEN)


def _truncate_to_token_budget(text: str, max_tokens: int) -> tuple[str, bool]:
    max_chars = max(1, max_tokens * _APPROX_CHARS_PER_TOKEN)
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars].rstrip() + "\n...(truncated to fit conversation history budget)", True


def _summarize_omitted_messages(messages: List[Dict[str, str]]) -> str:
    if not messages:
        return ""

    summary_lines = []
    omitted_head = messages[:_OMITTED_SUMMARY_MAX_MESSAGES]
    for message in omitted_head:
        content = " ".join(message["content"].split())
        if len(content) > _OMITTED_SUMMARY_SNIPPET_CHARS:
            content = content[:_OMITTED_SUMMARY_SNIPPET_CHARS].rstrip() + "..."
        summary_lines.append(f"- {message['role']}: {content}")

    remaining = len(messages) - len(omitted_head)
    if remaining > 0:
        summary_lines.append(f"- ... {remaining} more earlier messages compressed")

    return "\n".join(summary_lines)


def compact_conversation_history(
    messages: List[Dict[str, Any]],
    *,
    max_tokens: int = DEFAULT_HISTORY_TOKEN_BUDGET,
    per_message_tokens: int = DEFAULT_HISTORY_MESSAGE_TOKEN_BUDGET,
) -> List[Dict[str, str]]:
    """Keep recent conversation history within an approximate token budget.

    This is deterministic and local: it avoids another LLM call just to build
    context, while still making omitted history explicit to downstream agents.
    """
    valid_messages: List[Dict[str, str]] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        content = message.get("content")
        if role in {"user", "assistant", "system"} and isinstance(content, str) and content:
            valid_messages.append({"role": role, "content": content})

    compacted_reversed: List[Dict[str, str]] = []
    used_tokens = 0
    omitted_messages = 0
    omitted_tokens = 0
    omitted_details_reversed: List[Dict[str, str]] = []
    truncated_messages = 0

    for message in reversed(valid_messages):
        original_content = message["content"]
        content, truncated = _truncate_to_token_budget(original_content, per_message_tokens)
        tokens = _estimate_tokens(content)
        original_tokens = _estimate_tokens(original_content)

        if compacted_reversed and used_tokens + tokens > max_tokens:
            omitted_messages += 1
            omitted_tokens += original_tokens
            omitted_details_reversed.append(message)
            continue

        if not compacted_reversed and tokens > max_tokens:
            content, truncated = _truncate_to_token_budget(original_content, max_tokens)
            tokens = _estimate_tokens(content)

        if truncated:
            truncated_messages += 1

        compacted_reversed.append({"role": message["role"], "content": content})
        used_tokens += tokens

    compacted = list(reversed(compacted_reversed))
    if omitted_messages:
        omitted_summary = _summarize_omitted_messages(list(reversed(omitted_details_reversed)))
        summary_text = (
            "\nCompressed earlier messages:\n" + omitted_summary
            if omitted_summary
            else ""
        )
        compacted.insert(
            0,
            {
                "role": "system",
                "content": (
                    "Earlier conversation history was compressed to fit the context budget. "
                    f"omitted_messages={omitted_messages}, "
                    f"approx_omitted_tokens={omitted_tokens}, "
                    f"truncated_messages={truncated_messages}."
                    f"{summary_text}"
                ),
            },
        )
    elif truncated_messages:
        compacted.insert(
            0,
            {
                "role": "system",
                "content": (
                    "Some conversation messages were truncated to fit the context budget. "
                    f"truncated_messages={truncated_messages}."
                ),
            },
        )

    return compacted


@dataclass
class ConversationSession:
    """A single multi-turn conversation session."""
    session_id: str
    context: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)
    last_active: datetime = field(default_factory=datetime.now)

    def add_message(self, role: str, content: str):
        """Add a message to the session history."""
        get_db().save_conversation_message(self.session_id, role, content)
        self.last_active = datetime.now()

    def update_context(self, key: str, value: Any):
        """Update session context."""
        self.context[key] = value
        self.last_active = datetime.now()

    def get_history(self) -> List[Dict[str, Any]]:
        """Get message history."""
        messages = get_db().get_conversation_history(self.session_id)
        return compact_conversation_history(messages)

class ConversationManager:
    """Manages multiple conversation sessions with TTL."""
    
    def __init__(self, ttl_minutes: int = 30):
        self._sessions: Dict[str, ConversationSession] = {}
        self.ttl = timedelta(minutes=ttl_minutes)
        self._lock = threading.RLock()

    def get_or_create(self, session_id: str) -> ConversationSession:
        """Get an existing session or create a new one."""
        with self._lock:
            self._cleanup_expired()

            if session_id not in self._sessions:
                self._sessions[session_id] = ConversationSession(session_id=session_id)
                logger.info(f"Created new conversation session: {session_id}")
            else:
                # Update last active time
                self._sessions[session_id].last_active = datetime.now()

            return self._sessions[session_id]

    def add_message(self, session_id: str, role: str, content: str):
        """Add a message to a session."""
        session = self.get_or_create(session_id)
        session.add_message(role, content)

    def get_history(self, session_id: str) -> List[Dict[str, Any]]:
        """Get message history for a session."""
        session = self.get_or_create(session_id)
        return session.get_history()

    def clear(self, session_id: str):
        """Clear a session."""
        with self._lock:
            if session_id in self._sessions:
                del self._sessions[session_id]
                logger.info(f"Cleared conversation session: {session_id}")
        # We don't delete from DB here to keep history, or we could add a delete method.
        # For now, just clear from memory.

    def _cleanup_expired(self):
        """Remove expired sessions."""
        with self._lock:
            now = datetime.now()
            expired = [
                sid for sid, session in self._sessions.items()
                if now - session.last_active > self.ttl
            ]
            for sid in expired:
                del self._sessions[sid]
                logger.info(f"Cleaned up expired conversation session: {sid}")

# Global instance
conversation_manager = ConversationManager()
