"""MB Enterprise Chat (mock of the AWS-hosted agent): intake, A2A call to the orchestrator, presentation."""
from __future__ import annotations

import os
import uuid
from typing import Callable

import httpx

from ..config import get_settings
from ..contracts import Intake, Jurisdiction, OrchestratorResponse
from ..events import Tracer

VAGUE = {"help", "hi", "hello", "policy", "question", "?"}


def auth_headers() -> dict[str, str]:
    """Bearer header for the orchestrator / A2A gateway when it is protected with A2A_API_KEY."""
    key = os.environ.get("A2A_API_KEY", "")
    return {"Authorization": f"Bearer {key}"} if key else {}


def http_publisher(orchestrator_url: str) -> Callable[[dict], None]:
    """MB chat lives in another process (\"on AWS\"); it forwards its trace events to the orchestrator's hub."""
    client = httpx.Client(timeout=3.0, headers=auth_headers())

    def publish(event: dict) -> None:
        client.post(f"{orchestrator_url}/events/publish", json=event)

    return publish


def intake(message: str, tr: Tracer) -> Intake:
    tr.status("mb_chat", "working", "Checking request (no model call)")
    text = message.strip().rstrip("?!. ").lower()
    vague = not text or text in VAGUE or text in {"i have a question", "can you help", "help me"}
    result = Intake(
        needs_clarification=vague,
        clarifying_question="Could you give me a bit more detail, e.g. which policy or situation you are asking about?" if vague else "",
        normalized_query=message,
    )
    if result.needs_clarification:
        tr.status("mb_chat", "done", "Asking a clarifying question")
    return result


def call_orchestrator(query: str, requester: str, tr: Tracer, jurisdiction: Jurisdiction | None = None) -> OrchestratorResponse:
    """A2A (JSON-RPC 2.0 message/send) call to the orchestrator's A2A endpoint. In production the real MB Enterprise Chat on AWS
    makes this same call; the endpoint runs the workflow locally or forwards to the Foundry hosted agent (ORCHESTRATOR_MODE)."""
    s = get_settings()
    tr.message("mb_chat", "orchestrator", query, protocol="A2A message/send")
    tr.status("mb_chat", "working", "Waiting for orchestrator (A2A)")
    body = {
        "jsonrpc": "2.0",
        "id": tr.run_id,
        "method": "message/send",
        "params": {"message": {
            "kind": "message", "role": "user", "messageId": uuid.uuid4().hex,
            "parts": [{"kind": "text", "text": query}],
            "metadata": {"requester": requester, "run_id": tr.run_id},
        }},
    }
    if jurisdiction is not None:
        body["params"]["message"]["metadata"]["jurisdiction"] = jurisdiction
    resp = httpx.post(f"{s.orchestrator_url}/a2a", json=body, headers=auth_headers(), timeout=180.0)
    resp.raise_for_status()
    rpc = resp.json()
    if "error" in rpc:
        raise RuntimeError(f"A2A error: {rpc['error']}")
    for artifact in rpc["result"].get("artifacts", []):
        for part in artifact.get("parts", []):
            if part.get("kind") == "data":
                result = OrchestratorResponse(**part["data"])
                if jurisdiction is not None and result.status != "ERROR" and (
                    not result.risk or not result.risk.retrieval or result.risk.retrieval.jurisdiction != jurisdiction
                ):
                    raise ValueError("Orchestrator did not return scoped retrieval for the requested jurisdiction; update its regional configuration/code first")
                return result
    raise RuntimeError("A2A response did not contain a data part")


def present(response: OrchestratorResponse, tr: Tracer) -> str:
    tr.message("orchestrator", "mb_chat", response.status, protocol="A2A result")
    tr.status("mb_chat", "working", "Formatting reply (no model call)")
    if response.status == "ERROR":
        reply = f"Sorry, something went wrong while processing your request ({response.error})."
    else:
        reply = _template(response)
    tr.status("mb_chat", "done", "Reply delivered")
    return reply


def _template(r: OrchestratorResponse) -> str:
    if r.status == "ANSWERED":
        cites = "\n".join(f"- {c.title} ({c.source})" + (f": {c.url}" if c.url else "") for c in (r.risk.citations if r.risk else []))
        return f"{r.answer}\n\nSources:\n{cites}\n\nWas this helpful?"
    why = {
        "low_confidence": "I could not find enough reliable information to answer this confidently.",
        "high_risk": "This topic has legal or compliance implications that need an expert's judgement.",
        "low_quality": "I was not able to produce an answer of sufficient quality.",
    }.get(r.decision.trigger if r.decision else "", "This needs a human expert.")
    inc = r.incident
    if inc is None:
        raise ValueError("Escalated response is missing an incident acknowledgement")
    if inc.mock:
        return f"{why} POC acknowledgement: {inc.incident_id} ({inc.assignment_group}). No real ServiceNow incident was created or sent."
    return f"{why} I have forwarded your request to a human expert ({inc.assignment_group}). Your ticket is {inc.incident_id}; you will be contacted directly."
