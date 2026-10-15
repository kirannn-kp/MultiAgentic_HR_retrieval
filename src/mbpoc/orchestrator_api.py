"""Orchestrator service (A2A endpoint + live event stream for the UI). Run: uvicorn mbpoc.orchestrator_api:app --port 8001"""
from __future__ import annotations

import asyncio
import hmac
import json
import os
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import TypeAdapter, ValidationError

from .agents import hosted, servicenow
from .config import get_settings, load_criteria, thresholds
from .contracts import Jurisdiction, OrchestratorResponse
from .regional import REGIONS, load_catalog
from .events import AGENTS, EventHub, Tracer
from .workflow import handle_request

settings = get_settings()
hub = EventHub()
app = FastAPI(title="Risk Assessment Orchestrator", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"])

API_KEY = os.environ.get("A2A_API_KEY", "")
PUBLIC_PATHS = {"/health", "/.well-known/agent-card.json"}


@app.middleware("http")
async def require_api_key(request: Request, call_next):
    """When A2A_API_KEY is set (for example in the cloud deployment) every call except the agent card needs `Authorization: Bearer <key>`."""
    if API_KEY and request.url.path not in PUBLIC_PATHS and request.method != "OPTIONS":
        supplied = request.headers.get("authorization", "").removeprefix("Bearer ").strip() or request.headers.get("x-api-key", "")
        if not hmac.compare_digest(supplied, API_KEY):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
    return await call_next(request)

AGENT_CARD = {
    "name": "Risk Assessment Orchestrator",
    "description": "Answers HR/compliance consultations from Foundry IQ, scores confidence/risk/quality and escalates to a human via ServiceNow.",
    "url": f"{settings.orchestrator_url}/a2a",
    "version": "0.1.0",
    "protocolVersion": "0.3.0",
    "capabilities": {"streaming": False},
    "defaultInputModes": ["text/plain"],
    "defaultOutputModes": ["text/plain", "application/json"],
    "skills": [{"id": "consult", "name": "HR consultation", "description": "Answer a policy question or escalate to a human.", "tags": ["hr", "risk", "foundry-iq"]}],
}


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "mock": settings.mock}


@app.get("/.well-known/agent-card.json")
def agent_card() -> dict:
    return AGENT_CARD


@app.get("/api/meta")
def meta() -> dict:
    c_min, q_min = thresholds()
    return {
        "mock": settings.mock, "agents": AGENTS, "thresholds": {"confidence_min": c_min, "quality_min": q_min},
        "criteria": load_criteria()["criteria"], "model": settings.model, "knowledge_base": settings.kb_name,
        "regional_retrieval": settings.regional_retrieval,
        "jurisdictions": [
            {"value": region, "label": label, "approved_urls": len(load_catalog().group(region))}
            for region, label in REGIONS.items()
        ] if settings.regional_retrieval else [],
    }


@app.get("/api/incidents")
def incidents() -> list[dict]:
    return servicenow.INCIDENTS


@app.post("/events/publish")
async def publish(request: Request) -> dict:
    hub.publish(await request.json())
    return {"ok": True}


@app.get("/events/history")
def history(after: int = 0) -> list[dict]:
    return hub.history(after)


@app.get("/events")
async def events(request: Request) -> StreamingResponse:
    queue = hub.subscribe()

    async def stream():
        try:
            yield "retry: 2000\n\n"
            while not await request.is_disconnected():
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                    yield f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            hub.unsubscribe(queue)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@app.post("/a2a")
async def a2a(request: Request) -> dict:
    rpc = await request.json()
    rpc_id = rpc.get("id")
    if rpc.get("method") != "message/send":
        return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": -32601, "message": "Method not found"}}
    message = rpc.get("params", {}).get("message", {})
    meta_ = message.get("metadata", {})
    query = " ".join(p.get("text", "") for p in message.get("parts", []) if p.get("kind") == "text")
    if not query.strip():
        return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": -32602, "message": "Empty message"}}
    run_id = meta_.get("run_id") or uuid.uuid4().hex[:12]
    tr = Tracer(run_id, hub.publish)
    requester = meta_.get("requester", "anonymous")
    jurisdiction = None
    if settings.regional_retrieval:
        try:
            jurisdiction = TypeAdapter(Jurisdiction).validate_python(meta_.get("jurisdiction"))
        except ValidationError:
            tr.status("orchestrator", "error", "Select jurisdiction DE or EU")
            return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": -32602, "message": "Regional retrieval requires metadata.jurisdiction = DE or EU"}}
    elif meta_.get("jurisdiction") is not None:
        return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": -32602, "message": "Enable regional retrieval before supplying a jurisdiction"}}
    if os.environ.get("ORCHESTRATOR_MODE", "local").lower() == "foundry":
        # A2A front door in front of the Foundry HOSTED orchestrator (Foundry cannot expose hosted agents over A2A itself)
        try:
            tr.status("orchestrator", "working", "Waiting for Foundry workflow; stage trace is available after completion")
            callback = lambda ev: tr._publish({**ev, "run_id": run_id})
            response = (
                await asyncio.to_thread(hosted.call, query, requester, callback, jurisdiction)
                if settings.regional_retrieval else await asyncio.to_thread(hosted.call, query, requester, callback)
            )
        except Exception as exc:
            response = OrchestratorResponse(run_id=run_id, status="ERROR", error=f"{type(exc).__name__}: {exc}")
    else:
        response = (
            await asyncio.to_thread(handle_request, query, requester, tr, jurisdiction)
            if settings.regional_retrieval else await asyncio.to_thread(handle_request, query, requester, tr)
        )
    text = response.answer if response.status == "ANSWERED" else (response.incident.message if response.incident else response.error)
    return {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "result": {
            "kind": "task",
            "id": run_id,
            "contextId": run_id,
            "status": {"state": "completed" if response.status != "ERROR" else "failed"},
            "artifacts": [{
                "artifactId": uuid.uuid4().hex,
                "name": "orchestrator-response",
                "parts": [{"kind": "text", "text": text}, {"kind": "data", "data": response.model_dump(mode="json")}],
            }],
        },
    }
