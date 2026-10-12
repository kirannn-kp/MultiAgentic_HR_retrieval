"""Risk Assessment Orchestrator workflow."""
from __future__ import annotations

from .agents import evaluator, risk as risk_agent, servicenow
from .contracts import OrchestratorResponse
from .events import Tracer
from . import policy


def handle_request(query: str, requester: str, tr: Tracer) -> OrchestratorResponse:
    tr.status("orchestrator", "working", "A2A request received")
    risk = quality = None
    try:
        risk = risk_agent.assess(query, requester, tr)

        decision = policy.pre_gate(risk)
        if decision is None:
            quality = evaluator.evaluate(query, risk, tr)
            decision = policy.final_gate(risk, quality)
        else:
            tr.status("evaluator", "skipped", f"not needed: {decision.trigger}")
        tr.decision(decision.route, decision.trigger, decision.reasons)

        if decision.route == "ANSWER":
            tr.status("servicenow", "skipped", "no escalation needed")
            tr.status("orchestrator", "done", "answer approved")
            return OrchestratorResponse(run_id=tr.run_id, status="ANSWERED", answer=risk.draft_answer, decision=decision, risk=risk, quality=quality)

        ack = servicenow.escalate(query, requester, risk, quality, decision.trigger, decision.reasons, tr)
        tr.status("orchestrator", "escalated", f"human handover: {ack.incident_id}")
        return OrchestratorResponse(run_id=tr.run_id, status="ESCALATED", decision=decision, risk=risk, quality=quality, incident=ack)
    except Exception as exc:
        tr.status("orchestrator", "error", str(exc)[:200])
        return OrchestratorResponse(run_id=tr.run_id, status="ERROR", risk=risk, quality=quality, error=f"{type(exc).__name__}: {exc}")
