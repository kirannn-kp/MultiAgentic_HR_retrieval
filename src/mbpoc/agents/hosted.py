"""Call the orchestrator hosted on Microsoft Foundry (Agent Framework workflow) and return its structured result."""
from __future__ import annotations

import json
import threading
from typing import Callable, Optional

from ..config import get_settings
from ..contracts import OrchestratorResponse
from .jsonutil import extract_json

_client = None
_lock = threading.Lock()


def _openai_client():
    global _client
    with _lock:
        if _client is None:
            from azure.ai.projects import AIProjectClient
            from azure.identity import DefaultAzureCredential

            s = get_settings()
            project = AIProjectClient(endpoint=s.project_endpoint, credential=DefaultAzureCredential(), allow_preview=True)
            _client = project.get_openai_client(agent_name=s.hosted_agent)
        return _client


def call(query: str, requester: str, on_event: Optional[Callable[[dict], None]] = None, delay: float = 0.0) -> OrchestratorResponse:
    """Hosted agents are reachable only through their Foundry endpoint (Responses). The reply starts with a readable summary
    followed by a ```json block with the structured result; its `trace` is handed to `on_event` (optionally paced by `delay`)."""
    import time

    resp = _openai_client().responses.create(input=json.dumps({"question": query, "requester": requester}))
    result = OrchestratorResponse(**extract_json(resp.output_text.split("```json", 1)[-1]))
    if on_event:
        for ev in result.trace:
            if delay:
                time.sleep(delay)
            on_event({k: v for k, v in ev.items() if k not in ("id", "ts", "run_id")})
    return result
