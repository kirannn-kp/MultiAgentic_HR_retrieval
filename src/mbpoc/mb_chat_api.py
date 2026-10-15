"""MB Enterprise Chat mock service + demo UI. Run: uvicorn mbpoc.mb_chat_api:app --port 8000"""
from __future__ import annotations

import asyncio
import time
import uuid
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from .agents import mb_chat
from .agents.mb_chat import auth_headers
from .config import get_settings
from .contracts import Jurisdiction
from .events import Tracer

settings = get_settings()
app = FastAPI(title="MB Enterprise Chat (mock)", version="0.1.0")
publish = mb_chat.http_publisher(settings.orchestrator_url)

SCENARIOS = [
    {"id": "answer", "label": "Answered from the knowledge base", "message": "How many vacation days do I get per year and when do carried-over days expire?"},
    {"id": "low_conf", "label": "Escalation: not covered (low confidence)", "message": "Is there a sabbatical program or sponsored MBA study leave?"},
    {"id": "high_risk", "label": "Escalation: employment law (high risk)", "message": "What severance applies when we terminate an employee?"},
    {"id": "low_quality", "label": "Escalation: incomplete answer (low quality)", "message": "Can I get my unused vacation paid out in cash, and what is the tax on the payout?"},
    {"id": "abroad", "label": "Escalation: cross-border work (high risk)", "message": "Can I work from Spain for two months?"},
    {"id": "gifts", "label": "Answered: gifts and hospitality limit", "message": "What is the maximum value of a gift I can accept from a business partner?"},
    {"id": "clarify", "label": "Clarifying question (vague request)", "message": "help"},
]


class ChatRequest(BaseModel):
    message: str
    requester: str = "employee@mb-enterprise.example"
    jurisdiction: Jurisdiction | None = None


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "mock": settings.mock}


@app.get("/.well-known/agent-card.json")
def agent_card() -> dict:
    return {
        "name": "MB Enterprise Chat (mock)",
        "description": "Stand-in for the AWS-hosted MB Enterprise Chat agent. Forwards user queries to the Risk Assessment Orchestrator over A2A.",
        "url": f"{settings.mb_chat_url}/api/chat",
        "version": "0.1.0",
        "skills": [{"id": "chat", "name": "Employee chat", "tags": ["chat", "front-door"]}],
    }


@app.get("/api/config")
def config() -> dict:
    # the browser reaches the orchestrator through this service (/orch), so only one port has to be exposed
    return {"orchestrator_url": "/orch", "mock": settings.mock, "scenarios": SCENARIOS, "regional_retrieval": settings.regional_retrieval}


@app.get("/orch/api/meta")
async def orch_meta() -> JSONResponse:
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(f"{settings.orchestrator_url}/api/meta", headers=auth_headers())
    return JSONResponse(r.json(), status_code=r.status_code)


@app.get("/orch/events/history")
async def orch_history() -> JSONResponse:
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(f"{settings.orchestrator_url}/events/history", headers=auth_headers())
    return JSONResponse(r.json(), status_code=r.status_code)


@app.get("/orch/events")
async def orch_events() -> StreamingResponse:
    async def stream():
        async with httpx.AsyncClient(timeout=None) as client:
            try:
                async with client.stream("GET", f"{settings.orchestrator_url}/events", headers=auth_headers()) as r:
                    async for chunk in r.aiter_raw():
                        yield chunk
            except httpx.HTTPError:
                return

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/chat")
async def chat(req: ChatRequest) -> dict:
    run_id = uuid.uuid4().hex[:12]
    tr = Tracer(run_id, publish)
    return await asyncio.to_thread(_run, req, tr)


def _run(req: ChatRequest, tr: Tracer) -> dict:
    started = time.perf_counter()
    timings: dict[str, int] = {}
    tr.run_start(req.message, req.requester)
    tr.message("user", "mb_chat", req.message, protocol="chat")
    try:
        intake = mb_chat.intake(req.message, tr)
        timings["intake"] = int((time.perf_counter() - started) * 1000)
        if intake.needs_clarification:
            tr.run_end("CLARIFY")
            return {"run_id": tr.run_id, "status": "CLARIFY", "reply": intake.clarifying_question, "timings_ms": timings}
        if settings.regional_retrieval and req.jurisdiction is None:
            tr.status("mb_chat", "done", "Jurisdiction selection required")
            tr.run_end("CLARIFY")
            return {"run_id": tr.run_id, "status": "CLARIFY", "reply": "Please select Germany or European Union, then send the same question again.", "timings_ms": timings}
        if req.jurisdiction is not None and not settings.regional_retrieval:
            raise ValueError("Enable regional retrieval before supplying a jurisdiction")
        before_call = time.perf_counter()
        response = (
            mb_chat.call_orchestrator(req.message, req.requester, tr, req.jurisdiction)
            if settings.regional_retrieval else mb_chat.call_orchestrator(req.message, req.requester, tr)
        )
        timings["orchestrator"] = int((time.perf_counter() - before_call) * 1000)
        tr.timing("orchestrator", "round_trip", timings["orchestrator"])
        before_present = time.perf_counter()
        reply = mb_chat.present(response, tr)
        timings["presentation"] = int((time.perf_counter() - before_present) * 1000)
        timings["total"] = int((time.perf_counter() - started) * 1000)
        tr.run_end(response.status, response=response.model_dump(mode="json"), reply=reply)
        return {"run_id": tr.run_id, "status": response.status, "reply": reply, "response": response.model_dump(mode="json"), "timings_ms": timings}
    except httpx.HTTPError as exc:
        tr.status("mb_chat", "error", f"orchestrator unreachable: {exc}")
        tr.run_end("ERROR")
        return {"run_id": tr.run_id, "status": "ERROR", "reply": f"The orchestrator is not reachable at {settings.orchestrator_url}: {exc}"}
    except Exception as exc:
        tr.status("mb_chat", "error", str(exc)[:200])
        tr.run_end("ERROR")
        return {"run_id": tr.run_id, "status": "ERROR", "reply": f"Error: {type(exc).__name__}: {exc}"}
    finally:
        tr.timing("mb_chat", "total", int((time.perf_counter() - started) * 1000))


UI = Path(__file__).parent / "ui" / "console.html"


@app.get("/")
def index() -> FileResponse:
    return FileResponse(UI)
