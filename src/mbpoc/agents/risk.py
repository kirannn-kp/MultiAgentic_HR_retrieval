"""Risk Assessment Agent: KB-grounded draft + confidence + risk level."""
from __future__ import annotations

import json

from ..config import get_settings
from ..contracts import Citation, Jurisdiction, RiskResult
from ..events import Tracer
from . import mock_kb
from .foundry import get_runner
from .jsonutil import clamp01, extract_json

WEB_ONLY_MAX_CONFIDENCE = 0.7


def assess(query: str, requester: str, tr: Tracer, jurisdiction: Jurisdiction | None = None) -> RiskResult:
    s = get_settings()
    tr.status("risk", "working", "Retrieving from Foundry IQ and assessing risk")
    tr.message("orchestrator", "risk", query, protocol="Foundry Responses")
    tr.status("kb", "working", "knowledge_base_retrieve")
    try:
        if s.regional_retrieval:
            if jurisdiction is None:
                raise ValueError("Select Germany (DE) or European Union (EU) for regional retrieval")
            from .. import regional_retrieval

            if s.mock:
                scope = regional_retrieval.mock_scope(jurisdiction, tr)
                result = mock_kb.assess(query, tr)
                if result.citations and (
                    "mb-sharepoint-ks" not in scope.allowed_sources or any(citation.source != "sharepoint" for citation in result.citations)
                ):
                    raise ValueError("Offline regional answers require the shared sample index; other mock sources are outside this scope")
                scope.queried_sources = ["mb-sharepoint-ks"] if result.citations else []
                result.retrieval = scope
                tr.retrieval_scope(scope.model_dump())
            else:
                evidence = regional_retrieval.retrieve(query, jurisdiction, s, tr)
                payload = {
                    "question": query, "requester": requester, "jurisdiction": jurisdiction,
                    "evidence": evidence.documents,
                    "citations": [citation.model_dump() for citation in evidence.citations],
                }
                result = _parse(get_runner().call_with_evidence(s.agent_risk, json.dumps(payload), tr).text)
                allowed = {(citation.source, citation.url) for citation in evidence.citations}
                if any((citation.source, citation.url) not in allowed for citation in result.citations):
                    raise ValueError("Risk assessment cited evidence outside the scoped retrieval")
                if evidence.citations and not result.citations:
                    raise ValueError("Risk assessment did not cite the supplied evidence")
                result.citations = evidence.citations
                if not evidence.documents:
                    result.confidence = 0.0
                result.retrieval = evidence.scope
        elif s.mock:
            result = mock_kb.assess(query, tr)
        else:
            call = get_runner().call(s.agent_risk, json.dumps({"question": query, "requester": requester}), tr, "risk")
            result = _parse(call.text)
    except Exception as exc:
        tr.status("kb", "error", str(exc)[:200])
        tr.status("risk", "error", str(exc)[:200])
        raise
    tr.status("kb", "done", "retrieval finished")
    if result.citations and all(c.source == "web" for c in result.citations):
        result.confidence = min(result.confidence, WEB_ONLY_MAX_CONFIDENCE)  # web is lower trust than internal sources
    tr.status("risk", "done", f"confidence={result.confidence:.2f} risk={result.risk_level}")
    tr.scores(confidence=result.confidence, risk=result.risk_level)
    return result


def _parse(text: str) -> RiskResult:
    data = extract_json(text)
    level = str(data.get("risk_level", "HIGH")).upper()  # fail safe: unknown -> HIGH
    return RiskResult(
        draft_answer=str(data.get("draft_answer", "")),
        confidence=clamp01(data.get("confidence")),
        risk_level=level if level in ("LOW", "MEDIUM", "HIGH") else "HIGH",
        risk_reasons=[str(r) for r in data.get("risk_reasons", [])],
        applicable_area=str(data.get("applicable_area", "general")),
        citations=[Citation(**{k: v for k, v in c.items() if k in Citation.model_fields}) for c in data.get("citations", []) if isinstance(c, dict)],
    )
