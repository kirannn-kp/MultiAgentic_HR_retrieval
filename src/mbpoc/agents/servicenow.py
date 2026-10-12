"""ServiceNow Agent. The ServiceNow call itself is a stub that returns an acknowledgement (no real incident)."""
from __future__ import annotations

import itertools
import json
import threading
import time

from ..config import get_settings
from ..contracts import Ack, QualityResult, RiskResult
from ..events import Tracer
from . import mock_kb
from .foundry import get_runner
from .jsonutil import extract_json

_counter = itertools.count(1)
_lock = threading.Lock()
INCIDENTS: list[dict] = []   # in-memory "ServiceNow" for the demo UI

AREA_GROUPS = {"legal": "HR Legal", "compliance": "Group Compliance / DPO", "tax": "Tax Department"}


def create_incident_stub(payload: dict) -> Ack:
    """Swap this function for a real ServiceNow REST/MCP call later; the signature stays the same."""
    with _lock:
        number = f"INC{time.strftime('%y%m%d')}{next(_counter):04d}"
        INCIDENTS.append({"number": number, **payload})
    return Ack(
        incident_id=number,
        status="Created (POC acknowledgement - not sent to ServiceNow)",
        assignment_group=payload.get("assignment_group", ""),
        priority=payload.get("priority", ""),
        short_description=payload.get("short_description", ""),
        message=f"Incident {number} registered; a human expert will review the AI draft and contact the requester.",
    )


def escalate(query: str, requester: str, risk: RiskResult, quality: QualityResult | None, trigger: str, reasons: list[str], tr: Tracer) -> Ack:
    s = get_settings()
    tr.status("servicenow", "working", "Preparing incident")
    tr.message("orchestrator", "servicenow", f"escalate ({trigger})", protocol="Foundry Responses")
    context = {
        "question": query, "requester": requester, "trigger": trigger, "escalation_reasons": reasons,
        "ai_draft_for_human_approval": risk.draft_answer, "confidence": risk.confidence, "risk_level": risk.risk_level,
        "risk_reasons": risk.risk_reasons, "applicable_area": risk.applicable_area, "quality": quality.model_dump() if quality else None,
    }
    try:
        if s.mock:
            mock_kb.latency()
            draft = _template(context)
        else:
            draft = extract_json(get_runner().call(s.agent_servicenow, json.dumps(context), tr, "servicenow").text)
        payload = {
            "short_description": str(draft.get("short_description", f"AI consultation escalation: {query[:80]}"))[:160],
            "description": str(draft.get("description", json.dumps(context))),
            "priority": str(draft.get("priority", "3-Moderate")),
            "assignment_group": str(draft.get("assignment_group", "HR Service Desk")),
            "requester": requester,
        }
        ack = create_incident_stub(payload)
        tr.tool("servicenow", "create_incident", payload, ack.model_dump_json(), mock=True)
    except Exception as exc:
        tr.status("servicenow", "error", str(exc)[:200])
        raise
    tr.status("servicenow", "done", f"{ack.incident_id} acknowledged")
    tr.message("servicenow", "orchestrator", f"ack {ack.incident_id}", protocol="ack")
    return ack


def _template(ctx: dict) -> dict:
    area = ctx["applicable_area"]
    high = ctx["risk_level"] == "HIGH"
    priority = "2-High" if high and area in ("legal", "compliance") else "3-Moderate" if high else "4-Low"
    return {
        "short_description": f"[AI escalation: {ctx['trigger']}] {ctx['question'][:90]}",
        "description": (
            f"Question: {ctx['question']}\nRequester: {ctx['requester']}\nTrigger: {ctx['trigger']}\n"
            f"Reasons: {'; '.join(ctx['escalation_reasons'])}\nConfidence: {ctx['confidence']} | Risk: {ctx['risk_level']}\n"
            f"AI draft for human approval:\n{ctx['ai_draft_for_human_approval']}"
        ),
        "priority": priority,
        "assignment_group": AREA_GROUPS.get(area, "HR Service Desk"),
    }
