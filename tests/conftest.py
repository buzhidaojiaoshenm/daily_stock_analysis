"""Pytest-wide compatibility hooks for the local test environment."""

from __future__ import annotations

from contextlib import contextmanager
from concurrent.futures import Future
from threading import Thread, current_thread
from typing import Any, Generator

import anyio
import anyio.from_thread as anyio_from_thread


@contextmanager
def _start_blocking_portal_with_keepalive(
    backend: str = "asyncio",
    backend_options: dict[str, Any] | None = None,
    *,
    name: str | None = None,
) -> Generator[anyio.abc.BlockingPortal, Any, None]:
    """Start AnyIO's portal with a timer so thread-safe callbacks are serviced."""

    async def keep_event_loop_awake(portal: anyio_from_thread.BlockingPortal) -> None:
        while portal._event_loop_thread_id is not None:
            await anyio.sleep(0.05)

    async def run_portal() -> None:
        async with anyio_from_thread.BlockingPortal() as portal:
            portal._task_group.start_soon(keep_event_loop_awake, portal)
            if name is None:
                current_thread().name = f"{backend}-portal-{id(portal):x}"

            future.set_result(portal)
            await portal.sleep_until_stopped()

    def run_blocking_portal() -> None:
        if future.set_running_or_notify_cancel():
            try:
                anyio_from_thread.run_eventloop(
                    run_portal,
                    backend=backend,
                    backend_options=backend_options,
                )
            except BaseException as exc:
                if not future.done():
                    future.set_exception(exc)

    future: Future[anyio.abc.BlockingPortal] = Future()
    thread = Thread(target=run_blocking_portal, daemon=True, name=name)
    thread.start()
    try:
        cancel_remaining_tasks = False
        portal = future.result()
        try:
            yield portal
        except BaseException:
            cancel_remaining_tasks = True
            raise
        finally:
            try:
                portal.call(portal.stop, cancel_remaining_tasks)
            except RuntimeError:
                pass
    finally:
        thread.join()


anyio_from_thread.start_blocking_portal = _start_blocking_portal_with_keepalive
