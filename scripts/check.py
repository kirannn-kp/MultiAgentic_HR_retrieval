"""Verify the setup. Usage: python scripts/check.py [--smoke]   (--smoke also runs one real question end to end)"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import requests  # noqa: E402

from mbpoc.config import get_settings  # noqa: E402

failures = 0


def row(ok: bool, label: str, detail: str = "") -> None:
    global failures
    if not ok:
        failures += 1
    print(f"  [{'OK' if ok else 'PROBLEM'}] {label} {detail}")


if __name__ == "__main__":
    s = get_settings()
    row(True, "Mode:", "offline simulation (no Azure)" if s.mock else "live Microsoft Foundry")
    if s.mock:
        raise SystemExit("  Nothing to check in simulation mode. Run setup.ps1 to connect to Azure.")

    from azure.ai.projects import AIProjectClient
    from azure.ai.projects.models import PromptAgentDefinition
    from azure.identity import DefaultAzureCredential
    from mbpoc.infra import foundry_agents, search_admin

    cred = DefaultAzureCredential()
    try:
        client = AIProjectClient(endpoint=s.project_endpoint, credential=cred)
        names = {a.name for a in client.agents.list()}
        row(True, "Foundry project reachable")
        for n in (s.agent_risk, s.agent_evaluator, s.agent_servicenow, s.agent_mb_chat):
            row(n in names, f"agent {n}", "" if n in names else "- run setup.ps1 again")
        if s.hosted_agent in names:
            row(True, f"hosted orchestrator {s.hosted_agent}")
        else:
            print(f"  [OPTIONAL] hosted orchestrator {s.hosted_agent} - run deploy-hosted.ps1 if needed")
        if s.agent_risk in names:
            definition = client.agents.get(agent_name=s.agent_risk).versions.latest.definition
            if not isinstance(definition, PromptAgentDefinition):
                raise ValueError("Risk agent must be a prompt agent")
            row(foundry_agents._matches_kb(foundry_agents._kb_tool(definition), s), "risk agent knowledge-base binding")
        connection = client.connections.get(name=s.kb_connection)
        row(
            connection.target.split("?", 1)[0].rstrip("/") == s.mcp_endpoint.split("?", 1)[0].rstrip("/"),
            f"MCP connection {s.kb_connection}", connection.target,
        )
    except Exception as exc:
        row(False, "Foundry project", str(exc)[:250])

    try:
        regional_sources = {}
        hdr = {"Authorization": f"Bearer {cred.get_token('https://search.azure.com/.default').token}"}
        base = s.search_endpoint.rstrip("/")
        kb = requests.get(f"{base}/knowledgebases/{s.kb_name}?api-version={s.search_api_version}", headers=hdr, timeout=30)
        row(kb.status_code == 200, f"knowledge base {s.kb_name}", f"(HTTP {kb.status_code})")
        if kb.status_code == 200:
            for ks in kb.json().get("knowledgeSources", []):
                source = search_admin.get_resource(s, "knowledgesources", ks["name"])
                row(source is not None, f"knowledge source {ks['name']}")
                if source is not None:
                    regional_sources[ks["name"]] = source
                if source and source["kind"] == "searchIndex":
                    index = source["searchIndexParameters"]["searchIndexName"]
                    count = requests.get(f"{base}/indexes/{index}/docs/$count?api-version={s.search_api_version}", headers=hdr, timeout=30)
                    count.raise_for_status()
                    n = count.text.strip()
                    row(n.isdigit() and int(n) > 0, f"documents in index {index}", f"({n} chunks)")
            if s.regional_retrieval:
                from mbpoc.regional import load_catalog
                from mbpoc.regional_retrieval import require_preview

                require_preview(s)
                for jurisdiction in ("DE", "EU"):
                    scope = load_catalog().plan(jurisdiction, regional_sources)
                    row(True, f"regional scope {jurisdiction}", f"({scope.approved_url_count} approved URLs; excludes {', '.join(scope.excluded_sources)})")
    except Exception as exc:
        row(False, "Azure AI Search", str(exc)[:250])

    if "--smoke" in sys.argv:
        from mbpoc.events import Tracer
        from mbpoc.workflow import handle_request

        jurisdiction = "DE" if s.regional_retrieval else None
        r = handle_request("What is the maximum value of a gift I can accept from a business partner?", "check", Tracer("check", lambda e: None), jurisdiction)
        row(r.status != "ERROR", "test question", f"-> {r.status} {r.error}")
    raise SystemExit(1 if failures else 0)
