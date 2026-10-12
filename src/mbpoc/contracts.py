"""Typed messages exchanged between the agents."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

RiskLevel = Literal["LOW", "MEDIUM", "HIGH"]


class Citation(BaseModel):
    source: str = "unknown"          # sharepoint | web | erd | unknown
    title: str = ""
    snippet: str = ""
    url: Optional[str] = None


class RiskResult(BaseModel):
    draft_answer: str = ""
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    risk_level: RiskLevel = "HIGH"
    risk_reasons: list[str] = []
    applicable_area: str = "general"
    citations: list[Citation] = []


class QualityResult(BaseModel):
    score: float = Field(0.0, ge=0.0, le=1.0)
    groundedness: float = 0.0
    relevance: float = 0.0
    completeness: float = 0.0
    policy_compliance: float = 1.0
    issues: list[str] = []


class Ack(BaseModel):
    incident_id: str
    status: str = "Created"
    assignment_group: str = ""
    priority: str = ""
    short_description: str = ""
    message: str = ""
    mock: bool = True


class Decision(BaseModel):
    route: Literal["ANSWER", "ESCALATE"]
    trigger: str                      # low_confidence | high_risk | low_quality | ok
    reasons: list[str] = []


class OrchestratorResponse(BaseModel):
    run_id: str
    status: Literal["ANSWERED", "ESCALATED", "ERROR"]
    answer: str = ""
    decision: Optional[Decision] = None
    risk: Optional[RiskResult] = None
    quality: Optional[QualityResult] = None
    incident: Optional[Ack] = None
    error: str = ""
    trace: list[dict] = []   # agent/tool events, so a caller (e.g. the demo UI) can replay what happened inside a hosted run


class Intake(BaseModel):
    needs_clarification: bool = False
    clarifying_question: str = ""
    normalized_query: str = ""
