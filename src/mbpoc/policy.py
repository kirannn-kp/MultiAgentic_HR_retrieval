"""Deterministic routing gate (mirrors the process diagram). Pure functions -> easy to unit test and audit."""
from __future__ import annotations

from typing import Optional

from .config import thresholds
from .contracts import Decision, QualityResult, RiskResult


def pre_gate(risk: RiskResult, confidence_min: Optional[float] = None) -> Optional[Decision]:
    """Decision possible before running the evaluator: score low -> human; risk high -> human."""
    c_min = thresholds()[0] if confidence_min is None else confidence_min
    if risk.confidence < c_min:
        return Decision(route="ESCALATE", trigger="low_confidence", reasons=[f"confidence {risk.confidence:.2f} < {c_min:.2f}"])
    if risk.risk_level == "HIGH":
        return Decision(route="ESCALATE", trigger="high_risk", reasons=risk.risk_reasons or ["risk level HIGH"])
    return None


def final_gate(risk: RiskResult, quality: QualityResult, quality_min: Optional[float] = None) -> Decision:
    q_min = thresholds()[1] if quality_min is None else quality_min
    if quality.score < q_min:
        return Decision(route="ESCALATE", trigger="low_quality", reasons=[f"quality {quality.score:.2f} < {q_min:.2f}", *quality.issues])
    return Decision(route="ANSWER", trigger="ok", reasons=[f"confidence {risk.confidence:.2f}, risk {risk.risk_level}, quality {quality.score:.2f}"])
