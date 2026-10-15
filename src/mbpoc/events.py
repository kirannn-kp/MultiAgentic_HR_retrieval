"""Event hub + tracer. Every agent action is published as an event; the UI subscribes over SSE."""
from __future__ import annotations

import asyncio
import itertools
import threading
import time
from collections import deque
from typing import Any, Callable, Optional

import os

_HOSTED = os.environ.get("ORCHESTRATOR_MODE", "local").lower() == "foundry"
AGENTS = [
    {"id": "mb_chat", "label": "MB Enterprise Chat", "role": "Direct forwarding + reply formatting (no model calls)", "host": "AWS (simulated)"},
    {"id": "orchestrator", "label": "Risk Assessment Orchestrator", "role": "Routes by confidence, risk and quality",
     "host": "Foundry hosted agent · Agent Framework" if _HOSTED else "Foundry / local service"},
    {"id": "risk", "label": "Risk Assessment Agent", "role": "Draft answer + confidence + risk (Foundry IQ)", "host": "Foundry"},
    {"id": "kb", "label": "Foundry IQ Knowledge Base", "role": "SharePoint and web retrieval", "host": "Azure AI Search"},
    {"id": "evaluator", "label": "Answer Evaluator", "role": "Scores answer quality", "host": "Foundry"},
    {"id": "servicenow", "label": "ServiceNow Agent", "role": "Creates incident (acknowledgement only)", "host": "Foundry (mock tool)"},
]

STATES = ("idle", "working", "done", "skipped", "escalated", "error")


class EventHub:
    def __init__(self, history: int = 1000) -> None:
        self._history: deque[dict] = deque(maxlen=history)
        self._subs: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = []
        self._lock = threading.Lock()
        self._seq = itertools.count(1)

    def publish(self, event: dict[str, Any]) -> dict[str, Any]:
        event = {**event, "id": next(self._seq), "ts": event.get("ts") or time.time()}
        with self._lock:
            self._history.append(event)
            subs = list(self._subs)
        for loop, queue in subs:
            loop.call_soon_threadsafe(queue.put_nowait, event)
        return event

    def history(self, after: int = 0) -> list[dict]:
        with self._lock:
            return [e for e in self._history if e["id"] > after]

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        with self._lock:
            self._subs.append((asyncio.get_running_loop(), queue))
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        with self._lock:
            self._subs = [s for s in self._subs if s[1] is not queue]


class Tracer:
    """Emits structured events for one run. `publish` is either hub.publish or an HTTP forwarder."""

    def __init__(self, run_id: str, publish: Callable[[dict], Any]) -> None:
        self.run_id = run_id
        self._publish = publish

    def _emit(self, kind: str, **fields: Any) -> None:
        try:
            self._publish({"run_id": self.run_id, "kind": kind, "ts": time.time(), **fields})
        except Exception:  # tracing must never break the flow
            pass

    def run_start(self, query: str, requester: str = "") -> None:
        self._emit("run_start", query=query, requester=requester)

    def run_end(self, status: str, **extra: Any) -> None:
        self._emit("run_end", status=status, **extra)

    def status(self, agent: str, state: str, detail: str = "") -> None:
        self._emit("status", agent=agent, state=state, detail=detail)

    def message(self, src: str, dst: str, text: str, protocol: str = "") -> None:
        self._emit("message", src=src, dst=dst, text=text, protocol=protocol)

    def tool(self, agent: str, tool: str, args: Any = None, result: Any = None, ms: Optional[int] = None, mock: bool = False) -> None:
        self._emit("tool_call", agent=agent, tool=tool, args=_clip(args), result=_clip(result), ms=ms, mock=mock)

    def kb_hit(self, source: str, count: int = 1) -> None:
        self._emit("kb_hit", source=source, count=count)

    def scores(self, **scores: Any) -> None:
        self._emit("scores", **scores)

    def timing(self, agent: str, stage: str, duration_ms: int) -> None:
        self._emit("timing", agent=agent, stage=stage, duration_ms=duration_ms)

    def retrieval_scope(self, scope: dict) -> None:
        self._emit("retrieval_scope", **scope)

    def decision(self, route: str, trigger: str, reasons: list[str]) -> None:
        self._emit("decision", route=route, trigger=trigger, reasons=reasons)


def _clip(value: Any, limit: int = 1500) -> Any:
    if value is None:
        return None
    text = value if isinstance(value, str) else str(value)
    return text if len(text) <= limit else text[:limit] + " …"
