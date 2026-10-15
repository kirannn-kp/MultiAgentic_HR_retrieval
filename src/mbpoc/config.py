"""Central configuration: everything comes from environment variables (.env) and config/risk_criteria.yaml."""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

import yaml
from dotenv import load_dotenv

ROOT = Path(os.environ.get("MBPOC_ROOT") or Path(__file__).resolve().parents[2])
load_dotenv(ROOT / ".env")


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _csv(name: str) -> list[str]:
    return [p.strip() for p in _env(name).split(",") if p.strip()]


@dataclass(frozen=True)
class Settings:
    mock: bool
    project_endpoint: str
    project_resource_id: str
    model: str
    embedding_deployment: str
    embedding_model: str
    aoai_endpoint: str
    search_endpoint: str
    search_api_version: str
    kb_name: str
    kb_connection: str
    enable_web_source: bool
    web_allowed_domains: list[str]
    sharepoint_site_url: str
    sharepoint_site_name: str
    tenant_id: str
    sharepoint_app_id: str
    sharepoint_app_secret: str
    storage_resource_id: str
    erd_container: str
    agent_risk: str
    agent_evaluator: str
    agent_servicenow: str
    agent_mb_chat: str
    orch_host: str
    orch_port: int
    mb_host: str
    mb_port: int
    orchestrator_url: str
    mb_chat_url: str
    cors_origins: list[str]
    regional_retrieval: bool = False

    @property
    def hosted_agent(self) -> str:
        return os.environ.get("HOSTED_ORCHESTRATOR_AGENT", "risk-orchestrator")

    @property
    def mcp_endpoint(self) -> str:
        return f"{self.search_endpoint.rstrip('/')}/knowledgebases/{self.kb_name}/mcp?api-version={self.search_api_version}"


def _derive_aoai(project_endpoint: str) -> str:
    host = urlparse(project_endpoint).hostname or ""
    resource = host.split(".")[0]
    return f"https://{resource}.openai.azure.com" if resource else ""


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    project_endpoint = _env("PROJECT_ENDPOINT") or _env("FOUNDRY_PROJECT_ENDPOINT")  # the latter is injected into hosted agents
    mock_mode = _env("MOCK_MODE", "auto").lower()
    mock = (not project_endpoint) if mock_mode == "auto" else mock_mode in ("1", "true", "yes")
    orch_host, orch_port = _env("ORCHESTRATOR_HOST", "127.0.0.1"), int(_env("ORCHESTRATOR_PORT", "8001"))
    mb_host, mb_port = _env("MB_CHAT_HOST", "127.0.0.1"), int(_env("MB_CHAT_PORT", "8000"))
    return Settings(
        mock=mock,
        project_endpoint=project_endpoint,
        project_resource_id=_env("PROJECT_RESOURCE_ID"),
        model=_env("MODEL_DEPLOYMENT_NAME", "gpt-4.1-mini"),
        embedding_deployment=_env("EMBEDDING_DEPLOYMENT_NAME", "text-embedding-3-large"),
        embedding_model=_env("EMBEDDING_MODEL_NAME", "text-embedding-3-large"),
        aoai_endpoint=_env("AZURE_OPENAI_ENDPOINT") or _derive_aoai(project_endpoint),
        search_endpoint=_env("SEARCH_ENDPOINT"),
        search_api_version=_env("SEARCH_API_VERSION", "2026-08-01-preview"),
        kb_name=_env("KNOWLEDGE_BASE_NAME", "mb-enterprise-kb"),
        kb_connection=_env("KB_CONNECTION_NAME", "mb-kb-mcp"),
        enable_web_source=_env("ENABLE_WEB_SOURCE", "true").lower() in ("1", "true", "yes"),
        web_allowed_domains=_csv("WEB_ALLOWED_DOMAINS"),
        sharepoint_site_url=_env("SHAREPOINT_SITE_URL"),
        sharepoint_site_name=_env("SHAREPOINT_SITE_NAME", "MB-Enterprise-POC"),
        tenant_id=_env("M365_TENANT_ID"),
        sharepoint_app_id=_env("SHAREPOINT_APP_ID"),
        sharepoint_app_secret=_env("SHAREPOINT_APP_SECRET"),
        storage_resource_id=_env("STORAGE_RESOURCE_ID"),
        erd_container=_env("ERD_CONTAINER", "erd-policies"),
        agent_risk=_env("AGENT_RISK", "risk-assessment-agent"),
        agent_evaluator=_env("AGENT_EVALUATOR", "answer-evaluator-agent"),
        agent_servicenow=_env("AGENT_SERVICENOW", "servicenow-agent"),
        agent_mb_chat=_env("AGENT_MB_CHAT", "mb-enterprise-chat-agent"),
        orch_host=orch_host,
        orch_port=orch_port,
        mb_host=mb_host,
        mb_port=mb_port,
        orchestrator_url=_env("ORCHESTRATOR_URL", f"http://{orch_host}:{orch_port}").rstrip("/"),
        mb_chat_url=_env("MB_CHAT_URL", f"http://{mb_host}:{mb_port}").rstrip("/"),
        cors_origins=_csv("CORS_ORIGINS") or [f"http://{mb_host}:{mb_port}"],
        regional_retrieval=_env("REGIONAL_RETRIEVAL_ENABLED", "false").lower() in ("1", "true", "yes"),
    )


@lru_cache(maxsize=1)
def load_criteria() -> dict:
    with open(ROOT / "config" / "risk_criteria.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def thresholds() -> tuple[float, float]:
    t = load_criteria()["thresholds"]
    return float(t["confidence_min"]), float(t["quality_min"])


# Knowledge-source names (Foundry IQ) -> UI label
KS_SHAREPOINT = "mb-sharepoint-ks"
KS_WEB = "mb-web-ks"
KS_ERD = "mb-erd-ks"
KS_LABELS = {KS_SHAREPOINT: "sharepoint", KS_WEB: "web", KS_ERD: "erd"}
