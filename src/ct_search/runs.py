"""Async run execution — in-process manager with persistence and SSE fanout.

POST /api/runs schedules the executor as an asyncio task and returns
immediately; progress events are persisted (store.py) and fanned out to any
number of live SSE subscribers. Reconnects replay from the store and dedupe
by sequence number, so a dropped stream loses nothing.

In-process by design: no Redis, no worker fleet — the same zero-config
philosophy as the rest of the stack. Runs die with the process; startup marks
orphans as errored (see main.py lifespan).
"""

from __future__ import annotations

import asyncio
from typing import Any

import logfire

from ct_search import store
from ct_search.models import ResearchRequest
from ct_search.providers import run_research
from ct_search.settings import Settings

TERMINAL_EVENT_KINDS = ("run.completed", "run.failed")


class RunManager:
    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[dict[str, Any] | None]]] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def start_run(self, request: ResearchRequest, settings: Settings) -> str:
        run_id = store.new_run_id()
        store.create_run(run_id, request)
        task = asyncio.get_running_loop().create_task(self._execute(run_id, request, settings))
        self._tasks[run_id] = task
        task.add_done_callback(lambda _task: self._tasks.pop(run_id, None))
        return run_id

    async def _execute(self, run_id: str, request: ResearchRequest, settings: Settings) -> None:
        store.mark_running(run_id)
        self._record(run_id, "run.started", {"run_id": run_id})
        try:
            response = await run_research(
                request,
                settings,
                on_event=lambda kind, payload: self._record(run_id, kind, payload),
            )
            store.complete_run(run_id, response)
            self._record(
                run_id,
                "run.completed",
                {
                    "run_id": run_id,
                    "status": "done",
                    "rows": len(response.rows),
                    "is_demo": response.is_demo,
                    "elapsed_ms": response.elapsed_ms,
                },
            )
        except Exception as exc:  # noqa: BLE001 — surface, never crash the loop
            logfire.error(
                "run_failed {run_id} {error_type}",
                run_id=run_id,
                error_type=type(exc).__name__,
                error=str(exc)[:300],
            )
            store.fail_run(run_id, f"{type(exc).__name__}: {exc}")
            self._record(
                run_id,
                "run.failed",
                {"run_id": run_id, "status": "error", "error": str(exc)[:300]},
            )
        finally:
            self._close(run_id)

    def subscribe(self, run_id: str) -> asyncio.Queue[dict[str, Any] | None]:
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._subscribers.setdefault(run_id, set()).add(queue)
        return queue

    def unsubscribe(self, run_id: str, queue: asyncio.Queue[dict[str, Any] | None]) -> None:
        listeners = self._subscribers.get(run_id)
        if listeners is not None:
            listeners.discard(queue)
            if not listeners:
                self._subscribers.pop(run_id, None)

    def _record(self, run_id: str, kind: str, payload: dict[str, Any]) -> None:
        """Persist one event and push it to live subscribers (same loop)."""
        seq = store.append_event(run_id, kind, payload)
        event = {"seq": seq, "kind": kind, "payload": payload}
        for queue in tuple(self._subscribers.get(run_id, ())):
            queue.put_nowait(event)

    def _close(self, run_id: str) -> None:
        """Wake subscribers with a sentinel so their streams can end."""
        for queue in tuple(self._subscribers.get(run_id, ())):
            queue.put_nowait(None)


_MANAGER: RunManager | None = None


def get_run_manager() -> RunManager:
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = RunManager()
    return _MANAGER
