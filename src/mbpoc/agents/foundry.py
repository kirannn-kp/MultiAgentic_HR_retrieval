"""Thin wrapper over the Foundry Agent Service (azure-ai-projects 2.x Responses API)."""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from ..config import KS_LABELS, Settings, get_settings
from ..events import Tracer


@dataclass
class AgentCall:
    text: str
    tool_calls: list[dict] = field(default_factory=list)
    duration_ms: int = 0


class FoundryRunner:
    def __init__(self, settings: Settings) -> None:
        from azure.ai.projects import AIProjectClient
        from azure.identity import DefaultAzureCredential

        self._s = settings
        self._project = AIProjectClient(endpoint=settings.project_endpoint, credential=DefaultAzureCredential())
        self._openai = self._project.get_openai_client()

    def call(self, agent_name: str, user_input: str, tracer: Optional[Tracer] = None, agent_id: str = "") -> AgentCall:
        started = time.perf_counter()
        response = self._create(agent_name, user_input)
        elapsed = int((time.perf_counter() - started) * 1000)
        tool_calls = [c for c in (_describe(item) for item in (getattr(response, "output", None) or [])) if c]
        if tracer:
            for tc in tool_calls:
                tracer.tool(agent_id or agent_name, tc["tool"], tc.get("args"), tc.get("result"), tc.get("ms"))
                for label, hits in _source_hits(str(tc.get("result", ""))).items():
                    tracer.kb_hit(label, hits)
        return AgentCall(text=getattr(response, "output_text", "") or "", tool_calls=tool_calls, duration_ms=elapsed)

    def _create(self, agent_name: str, user_input: str) -> Any:
        try:
            return self._openai.responses.create(
                input=user_input,
                extra_body={"agent_reference": {"name": agent_name, "type": "agent_reference"}},
            )
        except Exception as exc:  # older preview SDKs used the "agent" key
            if "agent_reference" not in str(exc):
                raise
            return self._openai.responses.create(
                input=user_input,
                extra_body={"agent": {"name": agent_name, "type": "agent_reference"}},
            )


def _kb_source_order() -> list[str]:
    """Knowledge-source names in knowledge-base order; the MCP result refers to them by knowledgeSourceIndex."""
    global _ks_order
    if _ks_order is None:
        import requests
        from azure.identity import DefaultAzureCredential

        s = get_settings()
        try:
            token = DefaultAzureCredential().get_token("https://search.azure.com/.default").token
            url = f"{s.search_endpoint.rstrip('/')}/knowledgebases/{s.kb_name}?api-version={s.search_api_version}"
            body = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=20).json()
            _ks_order = [k["name"] for k in body.get("knowledgeSources", [])]
        except Exception:
            _ks_order = []
    return _ks_order


_ks_order: Optional[list[str]] = None


def _source_hits(result: str) -> dict[str, int]:
    """Count retrieved documents per knowledge source (sharepoint / web / erd)."""
    hits: dict[str, int] = {}
    try:
        docs = json.loads(result).get("documents", [])
        order = _kb_source_order()
        for d in docs:
            idx = int(d.get("knowledgeSourceIndex", -1))
            label = KS_LABELS.get(order[idx]) if 0 <= idx < len(order) else None
            if label:
                hits[label] = hits.get(label, 0) + 1
    except Exception:
        pass
    if not hits:
        for name, label in KS_LABELS.items():
            if name in result:
                hits[label] = result.count(name)
    return hits


def _describe(item: Any) -> Optional[dict]:
    kind = getattr(item, "type", "")
    if kind == "mcp_call":
        return {
            "tool": getattr(item, "name", "mcp_call"),
            "args": getattr(item, "arguments", None),
            "result": getattr(item, "output", None) or getattr(item, "error", None),
        }
    if kind == "web_search_call":
        action = getattr(item, "action", None)
        return {"tool": "web_search", "args": json.dumps(getattr(action, "__dict__", {}) or str(action), default=str), "result": getattr(item, "status", "")}
    if kind == "function_call":
        return {"tool": getattr(item, "name", "function"), "args": getattr(item, "arguments", None), "result": ""}
    return None


_runner: Optional[FoundryRunner] = None
_lock = threading.Lock()


def get_runner() -> FoundryRunner:
    global _runner
    with _lock:
        if _runner is None:
            _runner = FoundryRunner(get_settings())
        return _runner
