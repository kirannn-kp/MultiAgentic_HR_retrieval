"""Scenario tests in offline mock mode (no Azure needed)."""
import os

os.environ["MOCK_MODE"] = "true"
os.environ["MOCK_LATENCY_SEC"] = "0"
os.environ["PROJECT_ENDPOINT"] = ""

import pytest

from mbpoc.contracts import QualityResult, RiskResult
from mbpoc.events import Tracer
from mbpoc import policy
from mbpoc.workflow import handle_request


def run(query: str):
    events: list[dict] = []
    return handle_request(query, "tester", Tracer("t1", events.append)), events


def test_answerable_question_is_answered():
    r, events = run("How many vacation days do I get per year and when do carried-over days expire?")
    assert r.status == "ANSWERED", r.model_dump()
    assert r.risk.risk_level == "LOW" and r.quality.score >= 0.7
    assert any(e["kind"] == "kb_hit" for e in events)


def test_unknown_topic_escalates_on_low_confidence():
    r, _ = run("Is there a sabbatical program or sponsored MBA study leave?")
    assert r.status == "ESCALATED" and r.decision.trigger == "low_confidence"
    assert r.incident and r.incident.incident_id.startswith("INC")


def test_termination_is_high_risk():
    r, events = run("What severance applies when we terminate an employee?")
    assert r.status == "ESCALATED" and r.decision.trigger == "high_risk"
    assert any(e["kind"] == "status" and e["agent"] == "evaluator" and e["state"] == "skipped" for e in events)


def test_cross_border_work_is_high_risk():
    r, _ = run("Can I work from Spain for two months?")
    assert r.status == "ESCALATED" and r.risk.risk_level == "HIGH"


def test_incomplete_answer_escalates_on_low_quality():
    r, _ = run("Can I get my unused vacation paid out in cash, and what is the tax on the payout?")
    assert r.status == "ESCALATED" and r.decision.trigger == "low_quality", r.model_dump()


def test_policy_gate_order():
    low_conf = RiskResult(confidence=0.3, risk_level="HIGH")
    assert policy.pre_gate(low_conf).trigger == "low_confidence"
    assert policy.pre_gate(RiskResult(confidence=0.9, risk_level="HIGH")).trigger == "high_risk"
    ok = RiskResult(confidence=0.9, risk_level="MEDIUM")
    assert policy.pre_gate(ok) is None
    assert policy.final_gate(ok, QualityResult(score=0.9)).route == "ANSWER"
    assert policy.final_gate(ok, QualityResult(score=0.4)).trigger == "low_quality"
