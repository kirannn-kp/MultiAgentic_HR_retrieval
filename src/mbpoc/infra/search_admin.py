"""Azure AI Search admin (REST): Foundry IQ knowledge sources + knowledge base, and the Foundry project MCP connection."""
from __future__ import annotations

import os
import time

import requests
from azure.identity import DefaultAzureCredential

from ..config import KS_ERD, KS_SHAREPOINT, KS_WEB, Settings
from . import sp_sync

_cred = DefaultAzureCredential()


def _search_headers() -> dict:
    return {"Authorization": f"Bearer {_cred.get_token('https://search.azure.com/.default').token}", "Content-Type": "application/json"}


def _put(s: Settings, kind: str, name: str, body: dict) -> dict:
    url = f"{s.search_endpoint.rstrip('/')}/{kind}/{name}?api-version={s.search_api_version}"
    resp = requests.put(url, headers=_search_headers(), json=body, timeout=120)
    if resp.status_code >= 400:
        raise RuntimeError(f"PUT {kind}/{name} -> {resp.status_code}: {resp.text[:800]}")
    return resp.json() if resp.text else {}


def get_resource(s: Settings, kind: str, name: str) -> dict | None:
    url = f"{s.search_endpoint.rstrip('/')}/{kind}/{name}?api-version={s.search_api_version}"
    resp = requests.get(url, headers=_search_headers(), timeout=30)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


def _ensure(s: Settings, kind: str, name: str, body: dict, *, update_existing: bool) -> dict:
    existing = get_resource(s, kind, name)
    if existing is not None and not update_existing:
        print(f"  REUSED {kind}/{name} (unchanged)")
        return existing
    return _put(s, kind, name, body)


def _model(s: Settings, deployment: str, model: str) -> dict:
    return {"kind": "azureOpenAI", "azureOpenAIParameters": {"resourceUri": s.aoai_endpoint, "deploymentId": deployment, "modelName": model}}


def _ingestion(s: Settings) -> dict:
    return {
        "networkAccessMode": "public", "identity": None, "disableImageVerbalization": True,
        "embeddingModel": _model(s, s.embedding_deployment, s.embedding_model),
        "chatCompletionModel": None, "contentExtractionMode": "minimal", "ingestionSchedule": None,
    }


def sharepoint_source(s: Settings, site_url: str) -> dict:
    conn = f"SharePointOnlineEndpoint={site_url};ApplicationId={s.sharepoint_app_id};ApplicationSecret={s.sharepoint_app_secret};TenantId={s.tenant_id}"
    return {
        "name": KS_SHAREPOINT, "kind": "indexedSharePoint", "description": "HR / compliance policies in the SharePoint site",
        "indexedSharePointParameters": {"connectionString": conn, "containerName": "defaultSiteLibrary", "query": None, "ingestionParameters": _ingestion(s)},
    }


def web_source(s: Settings) -> dict:
    params: dict = {}
    if s.web_allowed_domains:
        params = {"domains": {"allowedDomains": [{"address": d, "includeSubpages": True} for d in s.web_allowed_domains]}}
    return {"name": KS_WEB, "kind": "web", "description": "Public web (lower trust than internal sources)", "webParameters": params}


def erd_source(s: Settings) -> dict:
    return {
        "name": KS_ERD, "kind": "azureBlob", "description": "ERD policy records (Enterprise Rules & Directives)",
        "azureBlobParameters": {
            "connectionString": f"ResourceId={s.storage_resource_id}", "containerName": s.erd_container, "folderPath": None,
            "isADLSGen2": False, "ingestionParameters": _ingestion(s),
        },
    }


def _index_exists(s: Settings) -> bool:
    return get_resource(s, "indexes", sp_sync.INDEX_NAME) is not None


def setup_knowledge_base(s: Settings, sharepoint_url: str = "", *, update_existing: bool = False) -> list[str]:
    existing = get_resource(s, "knowledgebases", s.kb_name)
    if existing is not None and not update_existing:
        print(f"  REUSED knowledge base '{s.kb_name}' and its sources (unchanged)")
        return [source["name"] for source in existing.get("knowledgeSources", [])]
    sources: list[str] = []
    site_url = sharepoint_url or s.sharepoint_site_url
    native = os.environ.get("SHAREPOINT_MODE", "sync").lower() == "native"
    if native and site_url and s.sharepoint_app_id and s.sharepoint_app_secret and s.tenant_id:
        _ensure(s, "knowledgesources", KS_SHAREPOINT, sharepoint_source(s, site_url), update_existing=update_existing); sources.append(KS_SHAREPOINT)
    elif not native and _index_exists(s):
        _ensure(s, "knowledgesources", KS_SHAREPOINT, sp_sync.knowledge_source_body(), update_existing=update_existing); sources.append(KS_SHAREPOINT)
    else:
        print("! SharePoint source skipped (run scripts/01_sync_sharepoint.py first, or set SHAREPOINT_MODE=native with the app settings)")
    if s.enable_web_source:
        _ensure(s, "knowledgesources", KS_WEB, web_source(s), update_existing=update_existing); sources.append(KS_WEB)
    if s.storage_resource_id:
        _ensure(s, "knowledgesources", KS_ERD, erd_source(s), update_existing=update_existing); sources.append(KS_ERD)
    if not sources:
        raise SystemExit("No knowledge source configured - nothing to put into the knowledge base.")
    _put(s, "knowledgebases", s.kb_name, {
        "name": s.kb_name,
        "description": "MB Enterprise HR / compliance knowledge (Foundry IQ)",
        "retrievalInstructions": "Use the SharePoint and ERD sources first for company policy questions; use web only for public/regulatory background.",
        "answerInstructions": "Answer only from retrieved content, cite the source document for every statement, and say so when the content does not answer the question.",
        "outputMode": os.environ.get("KB_OUTPUT_MODE", "answerSynthesis"),
        "knowledgeSources": [{"name": n} for n in sources],
        "models": [_model(s, s.model, s.model)],
        "retrievalReasoningEffort": {"kind": "low"},
    })
    return sources


def ingestion_status(s: Settings, name: str) -> dict:
    url = f"{s.search_endpoint.rstrip('/')}/knowledgesources/{name}/status?api-version={s.search_api_version}"
    return requests.get(url, headers=_search_headers(), timeout=60).json()


def wait_for_ingestion(s: Settings, names: list[str], timeout_s: int = 600) -> None:
    deadline = time.time() + timeout_s
    pending = [n for n in names if n != KS_WEB]
    while pending and time.time() < deadline:
        for n in list(pending):
            st = ingestion_status(s, n)
            sync = st.get("lastSynchronizationState") or {}
            if sync.get("endTime"):
                print(f"  {n}: ingested (updated={sync.get('itemsUpdatesProcessed')}, failed={sync.get('itemsUpdatesFailed')})")
                pending.remove(n)
        if pending:
            time.sleep(15)
    if pending:
        print(f"  still ingesting: {pending} (check later with scripts/05_check_env.py)")


def _reuse_connection(s: Settings, name: str, target: str, category: str, *, update_existing: bool) -> bool:
    url = f"https://management.azure.com{s.project_resource_id}/connections/{name}?api-version=2025-10-01-preview"
    token = _cred.get_token("https://management.azure.com/.default").token
    resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=60)
    if resp.status_code == 404:
        return False
    resp.raise_for_status()
    if update_existing:
        return False
    properties = resp.json()["properties"]
    actual = properties.get("target", "").split("?", 1)[0].rstrip("/")
    expected = target.split("?", 1)[0].rstrip("/")
    if actual != expected or properties.get("category") != category:
        raise ValueError(
            f"Connection '{name}' already exists with a different target or category. "
            "Choose a different connection name, or explicitly use -UpdateExisting to replace its definition."
        )
    print(f"  REUSED connection '{name}' (unchanged)")
    return True


def create_search_connection(s: Settings, *, update_existing: bool = False) -> None:
    """Project connection to the Search service: this is what makes the knowledge base appear under Build > Knowledge in the Foundry portal."""
    name = s.search_endpoint.split("//")[1].split(".")[0]
    if _reuse_connection(s, name, s.search_endpoint, "CognitiveSearch", update_existing=update_existing):
        return
    resp = requests.get(f"https://management.azure.com{s.project_resource_id.split('/providers/')[0]}/providers/Microsoft.Search/searchServices/{name}?api-version=2023-11-01",
                        headers={"Authorization": f"Bearer {_cred.get_token('https://management.azure.com/.default').token}"}, timeout=60)
    resp.raise_for_status()
    search_id = resp.json()["id"]
    token = _cred.get_token("https://management.azure.com/.default").token
    resp = requests.put(
        f"https://management.azure.com{s.project_resource_id}/connections/{name}?api-version=2025-10-01-preview",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": name, "type": "Microsoft.MachineLearningServices/workspaces/connections", "properties": {
            "authType": "AAD", "category": "CognitiveSearch", "target": s.search_endpoint.rstrip("/") + "/", "isSharedToAll": True,
            "metadata": {"ApiType": "Azure", "ResourceId": search_id, "ApiVersion": "2024-05-01-preview"}}},
        timeout=60,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"Creating Search connection failed -> {resp.status_code}: {resp.text[:400]}")
    print(f"Search connection '{name}' created (knowledge base is visible in the Foundry portal under Build > Knowledge)")


def create_mcp_connection(s: Settings, *, update_existing: bool = False) -> None:
    """RemoteTool connection that lets Foundry agents call the knowledge base MCP endpoint with the project's managed identity."""
    if _reuse_connection(s, s.kb_connection, s.mcp_endpoint, "RemoteTool", update_existing=update_existing):
        return
    token = _cred.get_token("https://management.azure.com/.default").token
    resp = requests.put(
        f"https://management.azure.com{s.project_resource_id}/connections/{s.kb_connection}?api-version=2025-10-01-preview",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": s.kb_connection, "type": "Microsoft.MachineLearningServices/workspaces/connections", "properties": {
            "authType": "ProjectManagedIdentity", "category": "RemoteTool", "target": s.mcp_endpoint,
            "isSharedToAll": True, "audience": "https://search.azure.com/", "metadata": {"ApiType": "Azure"}}},
        timeout=60,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"Creating connection failed -> {resp.status_code}: {resp.text[:600]}")
    print(f"Connection '{s.kb_connection}' -> {s.mcp_endpoint}")
