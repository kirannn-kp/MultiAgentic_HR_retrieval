"""Remove everything the setup created (agents, knowledge base, index, connections). Your Foundry project, Search service and model stay.
Usage: python scripts/cleanup.py        (asks for confirmation; add --yes to skip)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import requests  # noqa: E402
from azure.ai.projects import AIProjectClient  # noqa: E402
from azure.identity import DefaultAzureCredential  # noqa: E402

from mbpoc.config import KS_SHAREPOINT, KS_WEB, get_settings  # noqa: E402
from mbpoc.infra.sp_sync import INDEX_NAME  # noqa: E402

s = get_settings()
if s.mock:
    sys.exit("Nothing to clean up: no Azure project is configured.")
if "--yes" not in sys.argv and input(f"Delete the demo agents, knowledge base '{s.kb_name}' and index in {s.search_endpoint}? (yes/no): ").strip().lower() != "yes":
    sys.exit("Cancelled.")

cred = DefaultAzureCredential()
client = AIProjectClient(endpoint=s.project_endpoint, credential=cred)
for name in (s.agent_risk, s.agent_evaluator, s.agent_servicenow, s.agent_mb_chat, s.hosted_agent):
    try:
        client.agents.delete(agent_name=name)
        print(f"deleted agent {name}")
    except Exception:
        pass

hdr = {"Authorization": f"Bearer {cred.get_token('https://search.azure.com/.default').token}"}
base, v = s.search_endpoint.rstrip("/"), s.search_api_version
for path in (f"knowledgebases/{s.kb_name}", f"knowledgesources/{KS_SHAREPOINT}", f"knowledgesources/{KS_WEB}", f"indexes/{INDEX_NAME}"):
    r = requests.delete(f"{base}/{path}?api-version={v}", headers=hdr, timeout=60)
    print(f"{'deleted' if r.status_code < 300 else 'skipped (' + str(r.status_code) + ')'} {path}")

arm = {"Authorization": f"Bearer {cred.get_token('https://management.azure.com/.default').token}"}
for conn in (s.kb_connection, s.search_endpoint.split("//")[1].split(".")[0]):
    r = requests.delete(f"https://management.azure.com{s.project_resource_id}/connections/{conn}?api-version=2025-10-01-preview", headers=arm, timeout=60)
    print(f"{'deleted' if r.status_code < 300 else 'skipped (' + str(r.status_code) + ')'} connection {conn}")
print("Done. Run setup.ps1 again to recreate everything.")
