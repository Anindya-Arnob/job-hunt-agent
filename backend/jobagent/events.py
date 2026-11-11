"""Live progress: nodes emit events, the dashboard receives them over a WebSocket.

Thread-safe (agents run in threads and in the event loop). Also lets a run
wait for you, e.g. to solve a captcha, with a timeout.
"""
from __future__ import annotations

import asyncio
import threading
import time
from collections import deque
from typing import Any, Optional

_lock = threading.Lock()
_listeners: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = []
_history: deque = deque(maxlen=500)
_signals: dict[str, threading.Event] = {}


def emit(event: dict[str, Any], run_id: Optional[int] = None) -> None:
    ev = {"ts": time.time(), **event}
    if run_id is not None:
        ev["run_id"] = run_id
    with _lock:
        _history.append(ev)
        listeners = list(_listeners)
    for loop, q in listeners:
        try:
            loop.call_soon_threadsafe(q.put_nowait, ev)
        except RuntimeError:
            pass


def subscribe() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=1000)
    with _lock:
        _listeners.append((asyncio.get_running_loop(), q))
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    with _lock:
        _listeners[:] = [(l, x) for l, x in _listeners if x is not q]


def recent(run_id: Optional[int] = None, limit: int = 200) -> list[dict]:
    with _lock:
        items = [e for e in _history if run_id is None or e.get("run_id") == run_id]
    return items[-limit:]


def clear() -> None:
    with _lock:
        _history.clear()


# ---------------------------------------------------------- waiting for you ---
def signal(key: str) -> None:
    with _lock:
        ev = _signals.setdefault(key, threading.Event())
    ev.set()


async def wait_for(key: str, timeout: float) -> bool:
    with _lock:
        ev = _signals.setdefault(key, threading.Event())
        ev.clear()
    deadline = time.time() + timeout
    while time.time() < deadline:
        if ev.is_set():
            return True
        await asyncio.sleep(0.5)
    return False
