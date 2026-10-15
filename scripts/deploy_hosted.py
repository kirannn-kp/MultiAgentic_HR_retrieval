"""Deploy the orchestrator as a Foundry HOSTED agent (Microsoft Agent Framework). Run through deploy-hosted.ps1 or:
    python scripts/deploy_hosted.py

Needs: setup.ps1 completed, Azure Developer CLI (azd) 1.27.1 or newer, and `azd auth login` done once.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOSTED = ROOT / "foundry-hosted"
sys.path.insert(0, str(ROOT / "src"))

from mbpoc.config import get_settings  # noqa: E402
from mbpoc.infra.azcli import az  # noqa: E402

ENV_NAME = "mb-risk-orchestrator"
AZD_ENV = {**os.environ, "AZURE_DEV_USER_AGENT": "microsoft_foundry_skill"}


def azd(*args: str, cwd: Path = HOSTED, check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    exe = shutil.which("azd")
    if not exe:
        sys.exit("The Azure Developer CLI (azd) is not installed. Install it with:  winget install Microsoft.Azd   then open a NEW terminal and run this again.")
    res = subprocess.run([exe, *args], cwd=cwd, env=AZD_ENV, text=True, capture_output=capture)
    if check and res.returncode:
        sys.exit(f"\n`azd {' '.join(args[:3])}` failed." + (f"\n{res.stdout}\n{res.stderr}" if capture else " See the messages above."))
    return res


def ensure_role(principal_id: str, role: str, scope: str) -> None:
    existing = az([
        "role", "assignment", "list", "--assignee-object-id", principal_id,
        "--scope", scope, "--query", f"[?roleDefinitionName=='{role}'].id",
    ])
    if existing:
        print(f"  REUSED {role} ({scope.rsplit('/', 1)[-1]})")
        return
    az([
        "role", "assignment", "create", "--assignee-object-id", principal_id,
        "--assignee-principal-type", "ServicePrincipal", "--role", role, "--scope", scope,
    ])
    print(f"  GRANTED {role} ({scope.rsplit('/', 1)[-1]})")


def main() -> None:
    s = get_settings()
    if s.mock or not s.project_endpoint:
        sys.exit("Run setup.ps1 first (it connects the project to your Azure environment).")

    print("[1/4] Checking tools")
    ver = azd("version", capture=True).stdout
    print(f"  {ver.strip().splitlines()[0]}")
    if azd("auth", "login", "--check-status", check=False, capture=True).returncode:
        sys.exit("\nYou are not signed in to azd. Run this once, finish the sign-in in the browser, then run deploy-hosted.ps1 again:\n    azd auth login --use-device-code")
    if "azure.ai.agents" not in azd("extension", "list", "--installed", capture=True, check=False).stdout:
        print("  Installing the azd Foundry agents extension ...")
        azd("extension", "install", "azure.ai.agents")

    print("[2/4] Preparing the deployment package")
    sub = az(["account", "show"])
    parts = s.project_resource_id.split("/")
    rg, account, project = parts[4], parts[8], parts[10]
    template = (HOSTED / "azure.yaml.template").read_text(encoding="utf-8")
    for key, val in {
        "__PROJECT_ENDPOINT__": s.project_endpoint, "__SEARCH_ENDPOINT__": s.search_endpoint, "__SEARCH_API_VERSION__": s.search_api_version,
        "__KB_NAME__": s.kb_name, "__KB_CONNECTION__": s.kb_connection, "__MODEL__": s.model, "__AGENT_RISK__": s.agent_risk,
        "__AGENT_EVALUATOR__": s.agent_evaluator, "__AGENT_SERVICENOW__": s.agent_servicenow, "__AGENT_MB_CHAT__": s.agent_mb_chat,
        "__REGIONAL_RETRIEVAL__": "true" if s.regional_retrieval else "false",
    }.items():
        template = template.replace(key, val)
    (HOSTED / "azure.yaml").write_text(template, encoding="utf-8")
    subprocess.run([sys.executable, str(HOSTED / "sync_hosted.py")], check=True)

    envs = azd("env", "list", "--output", "json", capture=True, check=False).stdout
    if ENV_NAME not in envs:
        azd("env", "new", ENV_NAME, "--subscription", sub["id"], "--location", az(["cognitiveservices", "account", "show", "-g", rg, "-n", account])["location"], "--no-prompt")
    azd("env", "select", ENV_NAME, check=False)
    values = {
        "AZURE_SUBSCRIPTION_ID": sub["id"], "AZURE_TENANT_ID": sub["tenantId"], "AZURE_RESOURCE_GROUP": rg,
        "AZURE_AI_ACCOUNT_NAME": account, "AZURE_AI_PROJECT_NAME": project, "AZURE_AI_PROJECT_ID": s.project_resource_id,
        "AZURE_AI_PROJECT_ENDPOINT": s.project_endpoint, "FOUNDRY_PROJECT_ENDPOINT": s.project_endpoint,
        "AZURE_AI_PROJECT_CONNECTIONS_PROJECT_ENDPOINT": s.project_endpoint, "AZURE_AI_MODEL_DEPLOYMENT_NAME": s.model,
        "AZURE_OPENAI_ENDPOINT": s.aoai_endpoint + "/", "USE_EXISTING_AI_PROJECT": "true", "AZD_AGENT_SKIP_ACR": "true", "AZD_FOUNDRY_ACR_MODE": "none",
    }
    for k, v in values.items():
        azd("env", "set", k, v, capture=True)

    print("[3/5] Deploying to Foundry (about 2 minutes) ...")
    azd("deploy", "--no-prompt")

    print("[4/5] Configuring hosted identity access")
    info = json.loads(azd("ai", "agent", "show", "risk-orchestrator", "--output", "json", capture=True).stdout)
    if s.regional_retrieval:
        principal_id = info.get("instance_identity", {}).get("principal_id")
        if not principal_id:
            raise ValueError("The hosted agent did not return an instance identity; regional Search permissions were not assigned")
        search_name = s.search_endpoint.split("//", 1)[-1].split(".", 1)[0]
        search_scope = az(["search", "service", "show", "-g", rg, "-n", search_name, "--query", "id"], json_output=False)
        ensure_role(principal_id, "Search Service Contributor", search_scope)
        ensure_role(principal_id, "Search Index Data Reader", search_scope)
        ensure_role(principal_id, "Foundry User", s.project_resource_id)
        print("  Azure RBAC can take several minutes to propagate before the first live invocation.")

    print("[5/5] Verifying")
    print(f"  Agent risk-orchestrator version {info.get('version')} is {info.get('status')}")
    print(f"  Open in Foundry: {info.get('playground_url', '(see Build > Agents in the Foundry portal)')}")
    env_text = (ROOT / ".env").read_text(encoding="utf-8").splitlines()
    (ROOT / ".env").write_text("\n".join("ORCHESTRATOR_MODE=foundry" if line.startswith("ORCHESTRATOR_MODE=") else line for line in env_text) + "\n", encoding="utf-8")
    print("  The demo now uses the hosted agent (ORCHESTRATOR_MODE=foundry in .env). Restart the demo with .\\start.ps1")


if __name__ == "__main__":
    main()
