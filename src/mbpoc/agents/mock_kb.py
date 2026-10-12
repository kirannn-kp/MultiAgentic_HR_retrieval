"""Offline stand-in for Foundry IQ: keyword retrieval over data/sharepoint and data/erd + rule-based risk scoring."""
from __future__ import annotations

import math
import os
import re
import time
from dataclasses import dataclass
from functools import lru_cache

from ..config import ROOT, load_criteria
from ..contracts import Citation, RiskResult
from ..events import Tracer

STOPWORDS = set(
    "a an the and or of to in on for with is are was were be been do does did can could should would will may might i me my we our you your "
    "how what when where which who why many much any get give have has had about at by from as it its this that these those there their "
    "per if not no yes please need want like also".split()
)
SYNONYMS = {
    "spain": "abroad", "france": "abroad", "italy": "abroad", "usa": "abroad", "country": "abroad", "countries": "abroad",
    "overseas": "abroad", "foreign": "abroad", "international": "abroad", "vacation": "leave", "holiday": "leave", "holidays": "leave",
    "fire": "terminate", "fired": "terminate", "dismiss": "terminate", "laid": "terminate", "termination": "terminate",
    "terminating": "terminate", "terminated": "terminate",
}


def latency() -> None:
    time.sleep(float(os.environ.get("MOCK_LATENCY_SEC", "0.8")))


def tokens(text: str) -> list[str]:
    out = []
    for raw in re.findall(r"[a-zA-Z0-9]+", text.lower()):
        if raw in STOPWORDS:
            continue
        w = SYNONYMS.get(raw, raw)
        if w.endswith("ies") and len(w) > 4:
            w = w[:-3] + "y"
        elif w.endswith("xes"):
            w = w[:-2]
        elif w.endswith("s") and not w.endswith(("ss", "us")) and len(w) > 3:
            w = w[:-1]
        out.append(w)
    return out


@dataclass
class Chunk:
    source: str
    title: str
    heading: str
    text: str
    toks: frozenset


@lru_cache(maxsize=1)
def _index() -> tuple[list[Chunk], dict[str, float]]:
    chunks: list[Chunk] = []
    for source in ("sharepoint", "erd"):
        for path in sorted((ROOT / "data" / source).glob("*.md")):
            raw = path.read_text(encoding="utf-8")
            title = (re.search(r"^#\s+(.+)$", raw, re.M) or [None, path.stem])[1].strip()
            for part in re.split(r"^##\s+", raw, flags=re.M)[1:]:
                heading, _, body = part.partition("\n")
                text = body.strip()
                chunks.append(Chunk(source, title, heading.strip(), text, frozenset(tokens(f"{title} {heading} {text}"))))
    n = len(chunks) or 1
    df: dict[str, int] = {}
    for c in chunks:
        for t in c.toks:
            df[t] = df.get(t, 0) + 1
    idf = {t: math.log(1 + n / d) for t, d in df.items()}
    return chunks, idf


def coverage(query_toks: set[str], doc_toks: set | frozenset, idf: dict[str, float]) -> float:
    total = sum(idf.get(t, 1.5) for t in query_toks)
    return (sum(idf.get(t, 1.5) for t in query_toks if t in doc_toks) / total) if total else 0.0


def retrieve(query: str, top_k: int = 2) -> tuple[list[tuple[Chunk, float]], float]:
    chunks, idf = _index()
    q = set(tokens(query))
    scored = sorted(((c, coverage(q, c.toks, idf)) for c in chunks), key=lambda x: x[1], reverse=True)
    top = [s for s in scored[:top_k] if s[1] > 0]
    union: set[str] = set()
    for c, _ in top:
        union |= c.toks
    return top, coverage(q, union, idf)


def classify_risk(query: str) -> tuple[str, list[str], str]:
    q = " ".join([query.lower()] + [SYNONYMS[w] for w in re.findall(r"[a-z]+", query.lower()) if w in SYNONYMS and SYNONYMS[w] in ("abroad", "terminate")])
    order = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
    level, reasons, area = "LOW", [], "general"
    for c in load_criteria()["criteria"]:
        hit = next((k for k in c.get("keywords", []) if k in q), None)
        if hit:
            reasons.append(f"{c['id']}: '{hit}' ({c['description'].split('.')[0]})")
            if order[c["level"]] > order[level] or (c["level"] == level and area == "general"):
                level, area = c["level"], c["area"]
    return level, reasons, area


def assess(query: str, tr: Tracer) -> RiskResult:
    latency()
    top, cov = retrieve(query)
    tr.tool("risk", "knowledge_base_retrieve", {"query": query}, f"{len(top)} chunk(s): " + "; ".join(f"{c.source}/{c.title} / {c.heading} ({s:.2f})" for c, s in top), mock=True)
    for source in {c.source for c, _ in top}:
        tr.kb_hit(source, sum(1 for c, _ in top if c.source == source))
    latency()
    level, reasons, area = classify_risk(query)
    citations = [Citation(source=c.source, title=f"{c.title} - {c.heading}", snippet=c.text[:400]) for c, _ in top]
    if top:
        draft = "\n\n".join(f"{c.text[:600]} [source: {c.title}]" for c, _ in top)
    else:
        draft = "The knowledge base does not contain information that answers this question."
    return RiskResult(
        draft_answer=draft,
        confidence=round(min(1.0, 0.1 + 0.9 * cov), 2) if top else 0.05,
        risk_level=level,
        risk_reasons=reasons,
        applicable_area=area,
        citations=citations,
    )
