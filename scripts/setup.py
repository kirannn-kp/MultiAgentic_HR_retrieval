"""Guided one-time setup. Run through setup.ps1 (Windows) or:  python scripts/setup.py

What it does (each step is safe to repeat):
  1. finds your Foundry project, Azure AI Search service and model deployment, and writes the .env file
  2. enables sign-in (Entra) on the Search service and grants the access rights the agents need
  3. loads the sample HR / compliance documents into the Search index ("SharePoint" source)
  4. creates the Foundry IQ knowledge base (SharePoint + Web) and the connections
  5. creates the four Foundry agents
  6. checks that everything is in place
"""
from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mbpoc.infra.azcli import AzError, az  # noqa: E402

ENV_FILE = ROOT / ".env"


def say(msg: str = "") -> None:
    print(msg, flush=True)


def step(n: int, title: str) -> None:
    say(f"\n[{n}/6] {title}")


def ask(prompt: str, default: str = "") -> str:
    if "--yes" in sys.argv:
        return default
    value = input(f"  {prompt}" + (f" [{default}]" if default else "") + ": ").strip()
    return value or default


def read_env() -> dict[str, str]:
    values: dict[str, str] = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                values[k.strip()] = v.strip()
    return values


def write_env(values: dict[str, str]) -> None:
    template = (ROOT / ".env.example").read_text(encoding="utf-8")
    out, seen = [], set()
    for line in template.splitlines():
        m = re.match(r"^([A-Z_]+)=", line)
        if m and m.group(1) in values:
            out.append(f"{m.group(1)}={values[m.group(1)]}")
            seen.add(m.group(1))
        else:
            out.append(line)
    out += [f"{k}={v}" for k, v in values.items() if k not in seen]
    ENV_FILE.write_text("\n".join(out) + "\n", encoding="utf-8")


# ----------------------------------------------------------------------------------------------- 1. discovery
def discover(env: dict[str, str]) -> dict[str, str]:
    try:
        account = az(["account", "show"])
    except AzError:
        sys.exit("\nYou are not signed in to Azure. Run:  az login   and then start the setup again.")
    say(f"  Signed in as {account['user']['name']} (subscription: {account['name']})")

    endpoint = ask("Paste your Foundry project endpoint (Foundry portal > your project > Overview)", env.get("PROJECT_ENDPOINT", ""))
    m = re.match(r"https://([^.]+)\.services\.ai\.azure\.com/api/projects/([^/?#]+)", endpoint)
    if not m:
        sys.exit("  That does not look like a project endpoint. Expected: https://<name>.services.ai.azure.com/api/projects/<project>")
    account_name, project_name = m.group(1), m.group(2)

    found = [a for a in az(["cognitiveservices", "account", "list"]) if a["name"].lower() == account_name.lower()]
    if not found:
        sys.exit(f"  Could not find the Foundry resource '{account_name}' in the subscription you are signed in to ({account['name']}).\n"
                 "  Switch subscription with:  az account set --subscription <name or id>")
    acc = found[0]
    rg, sub = acc["resourceGroup"], acc["id"].split("/")[2]
    say(f"  Foundry resource: {acc['name']}  (resource group {rg}, {acc['location']})")

    deployments = az(["cognitiveservices", "account", "deployment", "list", "-g", rg, "-n", acc["name"]])
    chat = [d["name"] for d in deployments if d["properties"]["model"]["name"].startswith(("gpt-4.1", "gpt-4o", "gpt-5"))]
    if not chat:
        sys.exit("  No GPT model deployment found in this Foundry resource. Deploy 'gpt-4.1-mini' in the Foundry portal (Models + endpoints) and run setup again.")
    default_model = env.get("MODEL_DEPLOYMENT_NAME") if env.get("MODEL_DEPLOYMENT_NAME") in chat else ("gpt-4.1-mini" if "gpt-4.1-mini" in chat else chat[0])
    model = ask(f"Model deployment to use ({', '.join(chat[:5])})", default_model)

    services = az(["search", "service", "list", "-g", rg])
    if not services:
        sys.exit(f"  No Azure AI Search service in resource group '{rg}'. Create one (Basic tier or higher, same region) in the Azure portal and run setup again.")
    usable = [s for s in services if s["sku"]["name"] != "free"] or services
    if len(usable) > 1:
        say("  Azure AI Search services found:")
        for i, s in enumerate(usable, 1):
            say(f"    {i}. {s['name']} ({s['sku']['name']})")
        current = next((i for i, s in enumerate(usable, 1) if f"https://{s['name']}.search.windows.net" == env.get("SEARCH_ENDPOINT")), 1)
        search = usable[int(ask("Which one should the knowledge base use? (number)", str(current)) or current) - 1]
    else:
        search = usable[0]
    if search["sku"]["name"] == "free":
        say("  ! The free Search tier has tight limits; Basic is recommended.")
    say(f"  Search service: {search['name']} ({search['sku']['name']})")

    values = dict(env)
    values.update({
        "PROJECT_ENDPOINT": endpoint, "PROJECT_RESOURCE_ID": f"{acc['id']}/projects/{project_name}", "MODEL_DEPLOYMENT_NAME": model,
        "AZURE_OPENAI_ENDPOINT": f"https://{account_name}.openai.azure.com", "SEARCH_ENDPOINT": f"https://{search['name']}.search.windows.net",
        "MOCK_MODE": "auto", "_SUBSCRIPTION": sub, "_RG": rg, "_ACCOUNT_ID": acc["id"], "_SEARCH_ID": search["id"], "_LOCATION": acc["location"],
    })
    internal = {k: values.pop(k) for k in list(values) if k.startswith("_")}
    write_env(values)
    say("  Settings saved to .env")
    return internal


# ----------------------------------------------------------------------------------------------- 2. access
def configure_access(ids: dict[str, str], values: dict[str, str]) -> None:
    search_name = values["SEARCH_ENDPOINT"].split("//")[1].split(".")[0]
    rg = ids["_RG"]
    warnings: list[str] = []

    def attempt(label: str, args: list[str]) -> None:
        try:
            az(args, json_output=False)
            say(f"  OK  {label}")
        except AzError as exc:
            warnings.append(f"{label}: {exc}")
            say(f"  !!  {label}  ({str(exc)[:110]})")

    attempt("Search service accepts Entra (Azure AD) sign-in", ["search", "service", "update", "-g", rg, "-n", search_name, "--auth-options", "aadOrApiKey", "--aad-auth-failure-mode", "http401WithBearerChallenge"])
    svc = az(["search", "service", "show", "-g", rg, "-n", search_name])
    if not svc.get("identity"):
        attempt("Search service gets a managed identity", ["search", "service", "update", "-g", rg, "-n", search_name, "--identity-type", "SystemAssigned"])
        svc = az(["search", "service", "show", "-g", rg, "-n", search_name])
    search_mi = (svc.get("identity") or {}).get("principalId")
    project = az(["rest", "--method", "get", "--url", f"https://management.azure.com{values['PROJECT_RESOURCE_ID']}?api-version=2025-06-01"], check=False) or {}
    project_mi = (project.get("identity") or {}).get("principalId")
    me = az(["ad", "signed-in-user", "show"], check=False) or {}

    def grant(who: str | None, kind: str, role: str, scope: str, label: str) -> None:
        if not who:
            say(f"  !!  {label}  (identity not found)")
            warnings.append(label)
            return
        attempt(label, ["role", "assignment", "create", "--assignee-object-id", who, "--assignee-principal-type", kind, "--role", role, "--scope", scope])

    grant(search_mi, "ServicePrincipal", "Cognitive Services User", ids["_ACCOUNT_ID"], "Search service may call the model (Cognitive Services User)")
    grant(project_mi, "ServicePrincipal", "Search Index Data Reader", ids["_SEARCH_ID"], "Foundry project may read the knowledge base (Search Index Data Reader)")
    grant(me.get("id"), "User", "Search Index Data Contributor", ids["_SEARCH_ID"], "You may load documents (Search Index Data Contributor)")
    grant(me.get("id"), "User", "Search Service Contributor", ids["_SEARCH_ID"], "You may create the knowledge base (Search Service Contributor)")
    if warnings:
        say("\n  Some access rights could not be set automatically. Ask your Azure administrator (Owner role) to assign the items marked '!!'.")


# ----------------------------------------------------------------------------------------------- 3-5
def load_content_and_agents() -> None:
    from dotenv import load_dotenv

    load_dotenv(ENV_FILE, override=True)
    from mbpoc.config import get_settings

    get_settings.cache_clear()
    s = get_settings()
    from mbpoc.infra import foundry_agents, search_admin, sp_sync

    step(3, "Loading the sample HR / compliance documents")
    deadline = time.time() + 360
    while True:
        try:
            sp_sync.sync(s, "local")
            break
        except Exception as exc:
            if ("403" in str(exc) or "401" in str(exc)) and time.time() < deadline:
                say("  Waiting for the new access rights to take effect (this can take a few minutes) ...")
                time.sleep(30)
                continue
            raise

    step(4, "Creating the Foundry IQ knowledge base (SharePoint + Web)")
    sources = search_admin.setup_knowledge_base(s)
    say(f"  Knowledge base '{s.kb_name}' with sources: {', '.join(sources)}")
    search_admin.create_mcp_connection(s)
    try:
        search_admin.create_search_connection(s)
    except Exception as exc:
        say(f"  ! Portal connection skipped ({str(exc)[:120]}); the agents still work.")

    step(5, "Creating the Foundry agents")
    foundry_agents.create_agents(s)


def main() -> None:
    say("MB Enterprise consultation assistant - guided setup")
    say("=" * 55)
    step(1, "Finding your Azure resources")
    env = read_env()
    ids = discover(env)
    values = read_env()
    step(2, "Setting up access rights")
    configure_access(ids, values)
    load_content_and_agents()
    step(6, "Checking everything")
    import subprocess

    subprocess.run([sys.executable, str(ROOT / "scripts" / "check.py")])
    say("\nDone. Start the demo with:  .\\start.ps1   (or: python -m mbpoc.run_all)")
    say("Optional: run the orchestrator as a Foundry hosted agent with:  .\\deploy-hosted.ps1")


if __name__ == "__main__":
    main()
