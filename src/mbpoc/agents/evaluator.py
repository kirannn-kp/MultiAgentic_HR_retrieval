"""Answer Evaluator Agent (LLM-as-judge; heuristic version in mock mode)."""
from __future__ import annotations

import json
import re

from ..config import get_settings, load_criteria
from ..contracts import QualityResult, RiskResult
from ..events import Tracer
from . import mock_kb
from .foundry import get_runner
from .jsonutil import clamp01, extract_json


def evaluate(query: str, risk: RiskResult, tr: Tracer) -> QualityResult:
    s = get_settings()
    tr.status("evaluator", "working", "Scoring answer quality")
    tr.message("orchestrator", "evaluator", "question + draft + citations", protocol="Foundry Responses")
    try:
        if s.mock:
            mock_kb.latency()
            result = _heuristic(query, risk)
            tr.tool("evaluator", "score_answer", {"dimensions": ["groundedness", "relevance", "completeness", "policy_compliance"]}, result.model_dump_json(), mock=True)
        else:
            payload = {"question": query, "draft_answer": risk.draft_answer, "citations": [c.model_dump() for c in risk.citations]}
            result = _parse(get_runner().call(s.agent_evaluator, json.dumps(payload), tr, "evaluator").text)
    except Exception as exc:
        tr.status("evaluator", "error", str(exc)[:200])
        raise
    tr.status("evaluator", "done", f"quality={result.score:.2f}")
    tr.scores(quality=result.score)
    return result


def _weighted(r: QualityResult) -> float:
    w = load_criteria()["evaluation"]["weights"]
    score = sum(getattr(r, k) * v for k, v in w.items())
    # a failed critical dimension caps the score (same rule is stated in the evaluator agent instructions)
    return round(min(score, 0.6) if min(r.groundedness, r.relevance, r.completeness) < 0.6 else score, 2)


def _parse(text: str) -> QualityResult:
    d = extract_json(text)
    r = QualityResult(
        score=clamp01(d.get("score")),
        groundedness=clamp01(d.get("groundedness")),
        relevance=clamp01(d.get("relevance")),
        completeness=clamp01(d.get("completeness")),
        policy_compliance=clamp01(d.get("policy_compliance"), 1.0),
        issues=[str(i) for i in d.get("issues", [])],
    )
    return r


def _heuristic(query: str, risk: RiskResult) -> QualityResult:
    _, idf = mock_kb._index()
    draft = set(mock_kb.tokens(re.sub(r"\[source:[^\]]*\]", "", risk.draft_answer)))
    evidence: set[str] = set()
    for c in risk.citations:
        evidence |= set(mock_kb.tokens(c.snippet))
    q = set(mock_kb.tokens(query))
    groundedness = (len(draft & evidence) / len(draft)) if draft else 0.0
    relevance = mock_kb.coverage(q, draft, idf)
    clauses = [set(mock_kb.tokens(c)) for c in re.split(r"\band\b|[,;?]", query) if mock_kb.tokens(c)]
    answered = sum(1 for c in clauses if mock_kb.coverage(c, draft, idf) >= 0.75)
    completeness = answered / len(clauses) if clauses else 0.0
    issues = []
    if completeness < 1:
        issues.append("Not every part of the question is addressed by the retrieved evidence")
    if groundedness < 0.8:
        issues.append("Parts of the draft are not supported by the citations")
    r = QualityResult(groundedness=round(groundedness, 2), relevance=round(relevance, 2), completeness=round(completeness, 2), policy_compliance=1.0, issues=issues)
    r.score = _weighted(r)
    return r
