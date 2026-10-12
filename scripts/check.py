"""Verify the setup. Usage: python scripts/check.py [--smoke]   (--smoke also runs one real question end to end)"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import requests  # noqa: E402

from mbpoc.config import get_settings  # noqa: E402


def row(ok: bool, label: str, detail: str = "") -> None:
    print(f"  [{'OK' if ok else 'PROBLEM'}] {label} {detail}")


if __name__ == "__main__":
    s = get_settings()
    row(True, "Mode:", "offline simulation (no Azure)" if s.mock else "live Microsoft Foundry")
    if s.mock:
        raise SystemExit("  Nothing to check in simulation mode. Run setup.ps1 to connect to Azure.")

    from azure.ai.projects import AIProjectClient
    from azure.identity import DefaultAzureCredential

    cred = DefaultAzureCredential()
    try:
        client = AIProjectClient(endpoint=s.project_endpoint, credential=cred)
        names = {a.name for a in client.agents.list()}
        row(True, "Foundry project reachable")
        for n in (s.agent_risk, s.agent_evaluator, s.agent_servicenow, s.agent_mb_chat):
            row(n in names, f"agent {n}", "" if n in names else "- run setup.ps1 again")
        row(s.hosted_agent in names, f"hosted orchestrator {s.hosted_agent}", "" if s.hosted_agent in names else "- optional: run deploy-hosted.ps1")
    except Exception as exc:
        row(False, "Foundry project", str(exc)[:250])

    try:
        hdr = {"Authorization": f"Bearer {cred.get_token('https://search.azure.com/.default').token}"}
        base = s.search_endpoint.rstrip("/")
        kb = requests.get(f"{base}/knowledgebases/{s.kb_name}?api-version={s.search_api_version}", headers=hdr, timeout=30)
        row(kb.status_code == 200, f"knowledge base {s.kb_name}", f"(HTTP {kb.status_code})")
        if kb.status_code == 200:
            for ks in kb.json().get("knowledgeSources", []):
                row(True, f"knowledge source {ks['name']}")
            n = requests.get(f"{base}/indexes/mb-sharepoint-docs/docs/$count?api-version={s.search_api_version}", headers=hdr, timeout=30).text
            row(n.strip().isdigit() and int(n) > 0, "documents in the SharePoint index", f"({n.strip()} chunks)")
    except Exception as exc:
        row(False, "Azure AI Search", str(exc)[:250])

    if "--smoke" in sys.argv:
        from mbpoc.events import Tracer
        from mbpoc.workflow import handle_request

        r = handle_request("What is the maximum value of a gift I can accept from a business partner?", "check", Tracer("check", lambda e: None))
        row(r.status != "ERROR", "test question", f"-> {r.status} {r.error}")
