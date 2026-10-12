# Hosted on Microsoft Foundry (Responses protocol) · built with Microsoft Agent Framework.
"""Risk Assessment Orchestrator as an Agent Framework workflow.

    Intake -> RiskAssessment --(confidence ok, risk < HIGH)--> Evaluation --(quality ok)--> Answer
                    |                                              |
                    +--(low confidence / HIGH risk)--> Escalation <--(low quality)
                                                          (ServiceNow agent, acknowledgement only)

Each executor calls a Foundry prompt agent (risk-assessment-agent with the Foundry IQ knowledge base, answer-evaluator-agent,
servicenow-agent). Routing is the deterministic policy gate in mbpoc.policy. The result is returned as JSON text.

State travels between executors as plain JSON-compatible dicts, so the workflow can be checkpointed by the Foundry host
(custom classes are blocked by the checkpoint deserializer).
"""
import asyncio
import json
import os
import uuid
from typing import Any

os.environ.setdefault("MBPOC_ROOT", os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("MOCK_MODE", "false")

from agent_framework import Executor, Message, WorkflowBuilder, WorkflowContext, handler
from agent_framework_foundry_hosting import ResponsesHostServer
from dotenv import load_dotenv
from typing_extensions import Never

load_dotenv()

from mbpoc import policy  # noqa: E402
from mbpoc.agents import evaluator, risk, servicenow  # noqa: E402
from mbpoc.contracts import Decision, OrchestratorResponse, QualityResult, RiskResult  # noqa: E402
from mbpoc.events import Tracer  # noqa: E402

Case = dict[str, Any]  # {query, requester, run_id, events, risk, quality, decision}


def _tracer(case: Case) -> Tracer:
    return Tracer(case["run_id"], case["events"].append)


def _risk(case: Case) -> RiskResult:
    return RiskResult(**case["risk"])


def _quality(case: Case) -> QualityResult | None:
    return QualityResult(**case["quality"]) if case.get("quality") else None


def _decision(case: Case) -> Decision | None:
    return Decision(**case["decision"]) if case.get("decision") else None


def _response(case: Case, status: str, **extra: Any) -> str:
    result = OrchestratorResponse(
        run_id=case["run_id"], status=status, decision=_decision(case), risk=_risk(case), quality=_quality(case), trace=case["events"], **extra,
    )
    return f"{_summary(result)}\n\n---\nStructured result (used by MB Enterprise Chat and the trace view):\n```json\n{result.model_dump_json(indent=1)}\n```"


def _summary(r: OrchestratorResponse) -> str:
    """Plain-language summary shown first in the Foundry playground."""
    scores = [f"confidence {r.risk.confidence:.2f}", f"risk {r.risk.risk_level.lower()}"]
    scores.append(f"answer quality {r.quality.score:.2f}" if r.quality else "answer quality not evaluated")
    if r.status == "ANSWERED":
        sources = "\n".join(f"- {c.title} ({c.source})" for c in r.risk.citations)
        return f"**Answer approved** ({', '.join(scores)})\n\n{r.answer}\n\n**Sources**\n{sources}"
    why = {"low_confidence": "not enough reliable information", "high_risk": "high-risk topic", "low_quality": "answer quality below threshold"}.get(r.decision.trigger, r.decision.trigger)
    reasons = "; ".join(r.decision.reasons)
    return (
        f"**Escalated to a human expert** ({why})\n\n"
        f"- Reason: {reasons}\n- Ticket: {r.incident.incident_id} · {r.incident.assignment_group} · priority {r.incident.priority}\n"
        f"- Scores: {', '.join(scores)}\n"
        "- The AI draft is attached to the ticket for human approval and is not sent to the requester. (Proof of concept: acknowledgement only, nothing was sent to ServiceNow.)"
    )


class Intake(Executor):
    def __init__(self) -> None:
        super().__init__(id="intake")

    @handler
    async def run(self, messages: list[Message], ctx: WorkflowContext[dict]) -> None:
        user_text = next((m.text for m in reversed(messages) if m.role == "user" and m.text), "")
        requester, query = "anonymous", user_text.strip()
        if query.startswith("{"):  # callers may send {"question": "...", "requester": "..."}
            try:
                data = json.loads(query)
                query, requester = data.get("question", query), data.get("requester", requester)
            except json.JSONDecodeError:
                pass
        case: Case = {"query": query, "requester": requester, "run_id": uuid.uuid4().hex[:12], "events": [], "risk": None, "quality": None, "decision": None}
        _tracer(case).status("orchestrator", "working", "Request received by hosted workflow")
        await ctx.send_message(case)


class RiskAssessment(Executor):
    def __init__(self) -> None:
        super().__init__(id="risk_assessment")

    @handler
    async def run(self, case: dict, ctx: WorkflowContext[dict]) -> None:
        result = await asyncio.to_thread(risk.assess, case["query"], case["requester"], _tracer(case))
        case["risk"] = result.model_dump()
        pre = policy.pre_gate(result)  # None -> the evaluator still has to run
        case["decision"] = pre.model_dump() if pre else None
        await ctx.send_message(case)


class Evaluation(Executor):
    def __init__(self) -> None:
        super().__init__(id="evaluation")

    @handler
    async def run(self, case: dict, ctx: WorkflowContext[dict]) -> None:
        quality = await asyncio.to_thread(evaluator.evaluate, case["query"], _risk(case), _tracer(case))
        case["quality"] = quality.model_dump()
        case["decision"] = policy.final_gate(_risk(case), quality).model_dump()
        await ctx.send_message(case)


class Answer(Executor):
    def __init__(self) -> None:
        super().__init__(id="answer")

    @handler
    async def run(self, case: dict, ctx: WorkflowContext[Never, str]) -> None:
        tr, decision = _tracer(case), _decision(case)
        tr.decision(decision.route, decision.trigger, decision.reasons)
        tr.status("servicenow", "skipped", "no escalation needed")
        tr.status("orchestrator", "done", "answer approved")
        await ctx.yield_output(_response(case, "ANSWERED", answer=_risk(case).draft_answer))


class Escalation(Executor):
    def __init__(self) -> None:
        super().__init__(id="escalation")

    @handler
    async def run(self, case: dict, ctx: WorkflowContext[Never, str]) -> None:
        tr, decision, quality = _tracer(case), _decision(case), _quality(case)
        if quality is None:
            tr.status("evaluator", "skipped", f"not needed: {decision.trigger}")
        tr.decision(decision.route, decision.trigger, decision.reasons)
        ack = await asyncio.to_thread(
            servicenow.escalate, case["query"], case["requester"], _risk(case), quality, decision.trigger, decision.reasons, tr,
        )
        tr.status("orchestrator", "escalated", f"human handover: {ack.incident_id}")
        await ctx.yield_output(_response(case, "ESCALATED", incident=ack))


def build_workflow():
    intake, assess, evaluate, answer, escalate = Intake(), RiskAssessment(), Evaluation(), Answer(), Escalation()
    return (
        WorkflowBuilder(name="risk-assessment-orchestrator", start_executor=intake, output_executors=[answer, escalate])
        .add_edge(intake, assess)
        .add_edge(assess, evaluate, condition=lambda c: c["decision"] is None)
        .add_edge(assess, escalate, condition=lambda c: c["decision"] is not None)
        .add_edge(evaluate, answer, condition=lambda c: c["decision"]["route"] == "ANSWER")
        .add_edge(evaluate, escalate, condition=lambda c: c["decision"]["route"] == "ESCALATE")
        .build()
    )


def main() -> None:
    ResponsesHostServer(build_workflow().as_agent()).run()


if __name__ == "__main__":
    main()
