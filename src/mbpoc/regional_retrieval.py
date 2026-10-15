"""Scoped REST retrieval: exclude every source outside the selected jurisdiction."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass

import requests
from azure.identity import DefaultAzureCredential

from .config import KS_SHAREPOINT, Settings
from .contracts import Citation, Jurisdiction, RetrievalScope
from .events import Tracer
from .regional import SOURCE_NAMES, SourceCatalog, load_catalog

API_VERSION = "2026-08-01-preview"
_credential = DefaultAzureCredential()


@dataclass
class RegionalEvidence:
    documents: list[dict]
    citations: list[Citation]
    scope: RetrievalScope


def require_preview(s: Settings) -> None:
    if s.search_api_version.lower() != API_VERSION:
        raise ValueError(f"Regional source exclusion requires SEARCH_API_VERSION={API_VERSION}; no unrestricted fallback is used")


def attached_sources(s: Settings) -> dict[str, dict]:
    require_preview(s)
    token = _credential.get_token("https://search.azure.com/.default").token
    headers = {"Authorization": f"Bearer {token}"}
    base = s.search_endpoint.rstrip("/")
    kb = requests.get(f"{base}/knowledgebases/{s.kb_name}?api-version={API_VERSION}", headers=headers, timeout=30)
    kb.raise_for_status()
    sources = {}
    for source in kb.json()["knowledgeSources"]:
        name = source["name"]
        response = requests.get(f"{base}/knowledgesources/{name}?api-version={API_VERSION}", headers=headers, timeout=30)
        response.raise_for_status()
        sources[name] = response.json()
    return sources


def retrieval_request(query: str, scope: RetrievalScope, attached: dict[str, dict]) -> dict:
    return {
        "messages": [{"role": "user", "content": [{"type": "text", "text": query}]}],
        "outputMode": "extractiveData",
        "includeActivity": True,
        "knowledgeSourceParams": [
            {
                "knowledgeSourceName": name, "kind": definition["kind"],
                "neverQuerySource": name not in scope.allowed_sources,
                **({"alwaysQuerySource": True, "failOnError": True, "includeReferences": True, "includeReferenceSourceData": True}
                   if name in scope.allowed_sources else {}),
            }
            for name, definition in attached.items()
        ],
    }


def parse_evidence(body: dict, scope: RetrievalScope, catalog: SourceCatalog) -> RegionalEvidence:
    activity = {str(entry["id"]): entry for entry in body["activity"]}
    queried = sorted({entry["knowledgeSourceName"] for entry in activity.values() if entry.get("knowledgeSourceName")})
    if set(queried) - set(scope.allowed_sources):
        raise ValueError("Retrieval activity reports an excluded or unknown source; refusing the answer")
    if any(entry.get("error") for entry in activity.values()):
        raise ValueError("A knowledge source reported a retrieval error; refusing partial evidence")
    scope.queried_sources = queried
    documents = []
    for message in body["response"]:
        for part in message["content"]:
            if part["type"] == "text" and part["text"].strip():
                extracted = json.loads(part["text"])
                if not isinstance(extracted, list):
                    raise ValueError("Regional retrieval requires an extractiveData document list")
                documents.extend(extracted)
    references = {str(ref["id"]): ref for ref in body["references"]}
    citations = []
    for document in documents:
        reference = references[str(document["ref_id"])]
        entry = activity[str(reference["activitySource"])]
        source = entry["knowledgeSourceName"]
        if source not in scope.allowed_sources:
            raise ValueError("Evidence reference came from an excluded source")
        expected_type = "web" if source in SOURCE_NAMES.values() else "searchIndex"
        if reference["type"] != expected_type:
            raise ValueError(f"Evidence reference type does not match source {source}")
        data = reference.get("sourceData") or {}
        url = reference.get("url") or data.get("source_url") or reference.get("citationUrl")
        if reference["type"] == "web":
            region = next((region for region, name in SOURCE_NAMES.items() if name == source), None)
            if not isinstance(url, str) or not any(item.permits(url) for item in catalog.group(region or "")):
                raise ValueError(f"Web reference URL is outside the approved allowlist for {source}")
        content = document["content"]
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Retrieved document has no usable grounding content")
        citations.append(Citation(
            source="web" if reference["type"] == "web" else "sharepoint" if source == KS_SHAREPOINT else "internal",
            title=document.get("title") or reference.get("title") or str(document["ref_id"]),
            snippet=content, url=url,
        ))
    return RegionalEvidence(documents=documents, citations=citations, scope=scope)


def retrieve(query: str, jurisdiction: Jurisdiction, s: Settings, tr: Tracer) -> RegionalEvidence:
    started = time.perf_counter()
    catalog = load_catalog()
    attached = attached_sources(s)
    scope = catalog.plan(jurisdiction, attached)
    tr.retrieval_scope(scope.model_dump())
    request = retrieval_request(query, scope, attached)
    token = _credential.get_token("https://search.azure.com/.default").token
    response = requests.post(
        f"{s.search_endpoint.rstrip('/')}/knowledgebases/{s.kb_name}/retrieve?api-version={API_VERSION}",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=request, timeout=120,
    )
    response.raise_for_status()
    if response.status_code != 200:
        raise ValueError(f"Regional retrieval returned HTTP {response.status_code}; partial responses are not accepted")
    evidence = parse_evidence(response.json(), scope, catalog)
    tr.tool("risk", "regional_knowledge_retrieve", request, {
        "queried_sources": scope.queried_sources, "document_count": len(evidence.documents),
    })
    tr.retrieval_scope(scope.model_dump())
    tr.timing("kb", "regional_retrieval", int((time.perf_counter() - started) * 1000))
    for label in {citation.source for citation in evidence.citations}:
        tr.kb_hit(label, sum(citation.source == label for citation in evidence.citations))
    return evidence


def mock_scope(jurisdiction: Jurisdiction, tr: Tracer) -> RetrievalScope:
    catalog = load_catalog()
    attached = {item["name"]: item for item in catalog.web_definitions()}
    attached.update({
        "mb-sharepoint-ks": {"kind": "searchIndex"},
        "mb-web-ks": {"kind": "web"},  # The old combined web source is always excluded.
    })
    scope = catalog.plan(jurisdiction, attached)
    scope.mock = True
    tr.retrieval_scope(scope.model_dump())
    return scope
