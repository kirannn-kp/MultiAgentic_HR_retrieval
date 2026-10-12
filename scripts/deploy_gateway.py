"""Deploy the A2A gateway to Azure App Service, so a chat agent running elsewhere (for example on AWS) can call the orchestrator over A2A.
Run through deploy-gateway.ps1 or:  python scripts/deploy_gateway.py

The gateway forwards every A2A request to the Foundry hosted orchestrator (run deploy-hosted.ps1 first). Calls need an API key,
which this script generates and prints once (it is also saved in .env as A2A_GATEWAY_KEY).
"""
from __future__ import annotations

import hashlib
import secrets
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mbpoc.config import get_settings  # noqa: E402
from mbpoc.infra.azcli import az  # noqa: E402

PLAN = "mb-a2a-plan"


def set_env_value(key: str, value: str) -> None:
    path = ROOT / ".env"
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    out, done = [], False
    for line in lines:
        if line.startswith(f"{key}="):
            out.append(f"{key}={value}")
            done = True
        else:
            out.append(line)
    if not done:
        out.append(f"{key}={value}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def build_zip() -> Path:
    out = Path(tempfile.mkdtemp()) / "gateway.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(ROOT / "requirements.txt", "requirements.txt")
        for folder in ("src", "config"):
            for f in (ROOT / folder).rglob("*"):
                if f.is_file() and "__pycache__" not in f.parts and f.suffix != ".pyc":
                    z.write(f, f.relative_to(ROOT).as_posix())
    return out


def main() -> None:
    s = get_settings()
    if s.mock:
        sys.exit("Run setup.ps1 and deploy-hosted.ps1 first.")
    parts = s.project_resource_id.split("/")
    sub, rg, account_name = parts[2], parts[4], parts[8]
    account = az(["cognitiveservices", "account", "show", "-g", rg, "-n", account_name])
    location = account["location"]
    app = f"mb-a2a-gateway-{hashlib.sha1((sub + rg).encode()).hexdigest()[:6]}"
    host = f"{app}.azurewebsites.net"
    key = secrets.token_urlsafe(32)

    print("[1/5] Creating the App Service plan and web app ...")
    az(["appservice", "plan", "create", "-g", rg, "-n", PLAN, "-l", location, "--is-linux", "--sku", "B1"], json_output=False)
    if not az(["webapp", "list", "-g", rg, "--query", f"[?name=='{app}'].name", "-o", "tsv"], json_output=False).strip():
        az(["webapp", "create", "-g", rg, "-p", PLAN, "-n", app, "--runtime", "PYTHON:3.12"], json_output=False)

    print("[2/5] Configuring (settings, start command, API key) ...")
    settings = {
        "MOCK_MODE": "false", "ORCHESTRATOR_MODE": "foundry", "HOSTED_ORCHESTRATOR_AGENT": s.hosted_agent, "PROJECT_ENDPOINT": s.project_endpoint,
        "PROJECT_RESOURCE_ID": s.project_resource_id, "MODEL_DEPLOYMENT_NAME": s.model, "SEARCH_ENDPOINT": s.search_endpoint,
        "SEARCH_API_VERSION": s.search_api_version, "KNOWLEDGE_BASE_NAME": s.kb_name, "KB_CONNECTION_NAME": s.kb_connection,
        "AGENT_RISK": s.agent_risk, "AGENT_EVALUATOR": s.agent_evaluator, "AGENT_SERVICENOW": s.agent_servicenow, "AGENT_MB_CHAT": s.agent_mb_chat,
        "ORCHESTRATOR_URL": f"https://{host}", "A2A_API_KEY": key, "CORS_ORIGINS": "https://localhost",
        "SCM_DO_BUILD_DURING_DEPLOYMENT": "true", "WEBSITES_PORT": "8000",
    }
    az(["webapp", "config", "appsettings", "set", "-g", rg, "-n", app, "--settings", *[f"{k}={v}" for k, v in settings.items()]], json_output=False)
    az(["webapp", "config", "set", "-g", rg, "-n", app, "--startup-file", "cd src && python -m uvicorn mbpoc.orchestrator_api:app --host 0.0.0.0 --port 8000"], json_output=False)
    principal = az(["webapp", "identity", "assign", "-g", rg, "-n", app, "--query", "principalId", "-o", "tsv"], json_output=False).strip()
    az(["role", "assignment", "create", "--assignee-object-id", principal, "--assignee-principal-type", "ServicePrincipal", "--role", "Foundry User", "--scope", account["id"]], json_output=False)

    print("[3/5] Uploading the code and building it in Azure (3-6 minutes) ...")
    package = build_zip()
    az(["webapp", "deploy", "-g", rg, "-n", app, "--src-path", str(package), "--type", "zip"], json_output=False)
    shutil.rmtree(package.parent, ignore_errors=True)

    print("[4/5] Saving the address and key in .env")
    set_env_value("A2A_GATEWAY_URL", f"https://{host}")
    set_env_value("A2A_GATEWAY_KEY", key)

    print("[5/5] Done\n")
    print(f"  A2A endpoint : https://{host}/a2a")
    print(f"  Agent card   : https://{host}/.well-known/agent-card.json  (public)")
    print(f"  API key      : {key}   (send as  Authorization: Bearer <key>; also saved in .env)")
    print("  Give the endpoint and the key to the team that owns the MB Enterprise Chat. For production, replace the API key with Entra / OAuth sign-in.")


if __name__ == "__main__":
    main()
