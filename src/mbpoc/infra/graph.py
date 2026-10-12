"""Microsoft Graph helpers: create (or reuse) the SharePoint test site and upload the sample policy documents."""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path
from urllib.parse import quote, urlparse

import requests

from ..config import ROOT, Settings

GRAPH = "https://graph.microsoft.com/v1.0"
# Public client id of "Microsoft Graph Command Line Tools" (device-code sign-in, no app registration needed for the uploader)
GRAPH_CLI_CLIENT_ID = "14d82eec-204b-4c2f-b7e8-296a70dab67e"
SCOPES = ["https://graph.microsoft.com/Group.ReadWrite.All", "https://graph.microsoft.com/Sites.ReadWrite.All", "https://graph.microsoft.com/User.Read"]


class Graph:
    def __init__(self, tenant_id: str = "", scopes: list[str] | None = None) -> None:
        from azure.identity import DeviceCodeCredential

        self._scopes = scopes or SCOPES
        self._cred = DeviceCodeCredential(client_id=GRAPH_CLI_CLIENT_ID, tenant_id=tenant_id or "organizations")
        self._token = None

    def _headers(self) -> dict:
        if self._token is None or self._token.expires_on - time.time() < 120:
            self._token = self._cred.get_token(*self._scopes)
        return {"Authorization": f"Bearer {self._token.token}"}

    def request(self, method: str, path: str, **kw) -> requests.Response:
        url = path if path.startswith("http") else f"{GRAPH}{path}"
        resp = requests.request(method, url, headers={**self._headers(), **kw.pop("headers", {})}, timeout=60, **kw)
        if resp.status_code >= 400:
            raise RuntimeError(f"Graph {method} {path} -> {resp.status_code}: {resp.text[:400]}")
        return resp


class AzCliGraph(Graph):
    """Graph access with the Azure CLI's signed-in account for `tenant_id` (no separate app consent / device code).
    Works when the CLI has a subscription in that tenant: `az login --tenant <tenant>` first."""

    def __init__(self, tenant_id: str) -> None:
        self._tenant = tenant_id
        self._sub = self._find_subscription()
        self._token = None

    def _find_subscription(self) -> str:
        out = _az(["account", "list", "--all", "-o", "json"])
        subs = [a["id"] for a in json.loads(out) if a.get("tenantId") == self._tenant]
        if not subs:
            raise RuntimeError(f"Azure CLI has no subscription in tenant {self._tenant}; run: az login --tenant {self._tenant}")
        return subs[0]

    def _headers(self) -> dict:
        if self._token is None or self._token[1] - time.time() < 300:
            tok = json.loads(_az(["account", "get-access-token", "--subscription", self._sub, "--resource", "https://graph.microsoft.com", "-o", "json"]))
            self._token = (tok["accessToken"], time.time() + 1800)
        return {"Authorization": f"Bearer {self._token[0]}"}


def _az(args: list[str]) -> str:
    cmd = subprocess.list2cmdline(["az", *args]) if os.name == "nt" else ["az", *args]
    res = subprocess.run(cmd, capture_output=True, text=True, shell=(os.name == "nt"))
    if res.returncode:
        raise RuntimeError(f"az {' '.join(args[:2])} failed: {res.stderr.strip()[:300]}")
    return res.stdout


def site_from_url(g: Graph, site_url: str) -> dict:
    u = urlparse(site_url)
    return g.request("GET", f"/sites/{u.hostname}:{u.path.rstrip('/')}").json()


def ensure_team_site(g: Graph, name: str) -> dict:
    """Create a Microsoft 365 group (which provisions a SharePoint team site) or reuse an existing one."""
    nickname = re.sub(r"[^A-Za-z0-9]", "", name)[:60] or "mbpoc"
    found = g.request("GET", f"/groups?$filter=mailNickname eq '{nickname}'").json()["value"]
    if found:
        group_id = found[0]["id"]
    else:
        me = g.request("GET", "/me").json()
        group_id = g.request("POST", "/groups", json={
            "displayName": name, "description": "Sample policy library for the MB Enterprise multi-agent POC",
            "mailNickname": nickname, "groupTypes": ["Unified"], "mailEnabled": True, "securityEnabled": False,
            "visibility": "Private", "owners@odata.bind": [f"{GRAPH}/users/{me['id']}"],
        }).json()["id"]
    for _ in range(40):  # site provisioning can take a few minutes
        resp = requests.get(f"{GRAPH}/groups/{group_id}/sites/root", headers=g._headers(), timeout=60)
        if resp.status_code == 200:
            return resp.json()
        time.sleep(6)
    raise TimeoutError("SharePoint site was not provisioned in time; rerun the script in a few minutes.")


def upload_folder(g: Graph, site_id: str, local_dir: Path, remote_folder: str = "Policies") -> list[str]:
    drive_id = g.request("GET", f"/sites/{site_id}/drive").json()["id"]
    uploaded = []
    for path in sorted(local_dir.glob("*")):
        if path.is_file():
            g.request("PUT", f"/drives/{drive_id}/root:/{quote(remote_folder)}/{quote(path.name)}:/content", data=path.read_bytes(), headers={"Content-Type": "application/octet-stream"})
            uploaded.append(path.name)
    return uploaded


def setup_sharepoint(s: Settings) -> str:
    g = Graph(s.tenant_id)
    site = site_from_url(g, s.sharepoint_site_url) if s.sharepoint_site_url else ensure_team_site(g, s.sharepoint_site_name)
    files = upload_folder(g, site["id"], ROOT / "data" / "sharepoint")
    print(f"Uploaded {len(files)} documents to {site['webUrl']} (Documents/Policies)")
    return site["webUrl"]
