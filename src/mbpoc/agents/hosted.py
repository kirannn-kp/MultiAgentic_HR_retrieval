"""Call the orchestrator hosted on Microsoft Foundry (Agent Framework workflow) and return its structured result."""
from __future__ import annotations

import json
import threading
from typing import Callable, Optional

from ..config import get_settings
from ..contracts import Jurisdiction, OrchestratorResponse
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


def call(query: str, requester: str, on_event: Optional[Callable[[dict], None]] = None, jurisdiction: Jurisdiction | None = None) -> OrchestratorResponse:
    """Hosted agents are reachable only through their Foundry endpoint (Responses). The reply starts with a readable summary
    followed by a ```json block with the structured result; its completed `trace` is handed to `on_event` without delays."""
    payload = {"question": query, "requester": requester}
    if jurisdiction is not None:
        payload["jurisdiction"] = jurisdiction
    resp = _openai_client().responses.create(input=json.dumps(payload))
    result = OrchestratorResponse(**extract_json(resp.output_text.split("```json", 1)[-1]))
    if jurisdiction is not None and result.status != "ERROR" and (not result.risk or not result.risk.retrieval or result.risk.retrieval.jurisdiction != jurisdiction):
        raise ValueError("Hosted deployment did not return scoped retrieval. Configure and redeploy the updated regional orchestrator before using it.")
    if on_event:
        for ev in result.trace:
            on_event({k: v for k, v in ev.items() if k not in ("id", "run_id")})
    return result
