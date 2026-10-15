"""Approved websites and deterministic jurisdiction scopes for one knowledge base."""
from __future__ import annotations

from functools import lru_cache
from urllib.parse import unquote, urlparse

import yaml
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from .config import ROOT
from .contracts import Jurisdiction, RetrievalScope

REGIONS = {"DE": "Germany", "EU": "European Union"}
SOURCE_NAMES = {"DE": "mb-web-de-ks", "EU": "mb-web-eu-ks"}


def domain_address(url: str) -> str:
    parsed = urlparse(url)
    if (
        parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
        or parsed.port or parsed.query or parsed.fragment
        or "\\" in url or any(character.isspace() for character in url)
        or any(part in (".", "..") for part in unquote(parsed.path).split("/"))
    ):
        raise ValueError("Approved URLs must be HTTPS websites/paths without credentials, ports, query strings or fragments")
    return parsed.hostname.lower() + parsed.path.rstrip("/")


class ApprovedSource(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str
    region: Jurisdiction
    description: str
    url: str
    include_subpages: bool = True
    enabled: bool = True

    @field_validator("url")
    @classmethod
    def valid_url(cls, value: str) -> str:
        domain_address(value)
        return value

    @field_validator("name", "description")
    @classmethod
    def nonempty(cls, value: str) -> str:
        if not value:
            raise ValueError("Source name and description must not be empty")
        return value

    def permits(self, url: str) -> bool:
        candidate, approved = urlparse(url), urlparse(self.url)
        try:
            port = candidate.port
        except ValueError:
            return False
        if candidate.scheme != "https" or candidate.hostname != approved.hostname or candidate.username or candidate.password or port:
            return False
        path, base = unquote(candidate.path).rstrip("/"), unquote(approved.path).rstrip("/")
        if "\\" in url or any(character.isspace() for character in url) or any(part in (".", "..") for part in path.split("/")):
            return False
        return path == base or self.include_subpages and (not base or path.startswith(base + "/"))


class SourceCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = 1
    shared_sources: list[str] = ["mb-sharepoint-ks"]
    sources: list[ApprovedSource]

    @model_validator(mode="after")
    def unique_sources(self) -> "SourceCatalog":
        urls = [domain_address(source.url) for source in self.sources]
        if len(urls) != len(set(urls)):
            raise ValueError("Duplicate source URLs are not allowed")
        if set(self.shared_sources) & set(SOURCE_NAMES.values()):
            raise ValueError("Regional web sources cannot be designated as shared")
        return self

    def group(self, region: str) -> list[ApprovedSource]:
        return [source for source in self.sources if source.enabled and source.region == region]

    def web_definitions(self) -> list[dict]:
        definitions = []
        for region, name in SOURCE_NAMES.items():
            sources = self.group(region)
            if not sources:
                raise ValueError(f"No enabled approved websites for {REGIONS[region]}")
            definitions.append({
                "name": name, "kind": "web",
                "description": f"Customer-approved {REGIONS[region]} legal and regulatory websites. Use only for applicable jurisdiction and subject matter.",
                "webParameters": {"domains": {"allowedDomains": [
                    {"address": domain_address(source.url), "includeSubpages": source.include_subpages}
                    for source in sources
                ]}},
            })
        return definitions

    def validate_web_definition(self, region: str, actual: dict) -> None:
        expected = next(item for item in self.web_definitions() if item["name"] == SOURCE_NAMES[region])
        if actual.get("kind") != "web":
            raise ValueError(f"{expected['name']} must be a web knowledge source")
        allowed = actual.get("webParameters", {}).get("domains", {}).get("allowedDomains") or []
        actual_domains = {
            (domain_address(item["address"] if item["address"].startswith("https://") else "https://" + item["address"]), item.get("includeSubpages", False))
            for item in allowed
        }
        expected_domains = {(item["address"], item["includeSubpages"]) for item in expected["webParameters"]["domains"]["allowedDomains"]}
        if actual_domains != expected_domains:
            raise ValueError(f"{expected['name']} has an allowlist different from the local approved catalogue; synchronize it explicitly")

    def plan(self, jurisdiction: Jurisdiction | None, attached: dict[str, dict]) -> RetrievalScope:
        if jurisdiction is None or jurisdiction not in REGIONS:
            raise ValueError("Select Germany (DE) or European Union (EU) before regional retrieval")
        regions = ["DE", "EU"] if jurisdiction == "DE" else ["EU"]
        required = {SOURCE_NAMES[region] for region in regions}
        if not required <= attached.keys():
            raise ValueError("Regional sources are missing from the KB. Preview/apply scripts\\configure_regional.py before enabling live regional retrieval.")
        for region in regions:
            self.validate_web_definition(region, attached[SOURCE_NAMES[region]])
        shared = set(self.shared_sources) & attached.keys()
        if any(attached[name].get("kind") != "searchIndex" for name in shared):
            raise ValueError("Shared sources must be searchIndex sources; other source types are not supported by this scoped parser")
        permitted = required | shared
        return RetrievalScope(
            jurisdiction=jurisdiction, allowed_sources=sorted(permitted),
            excluded_sources=sorted(attached.keys() - permitted),
            approved_url_count=sum(len(self.group(region)) for region in regions),
        )


@lru_cache(maxsize=1)
def load_catalog() -> SourceCatalog:
    path = ROOT / "config" / "approved_web_sources.yaml"
    with path.open(encoding="utf-8") as source:
        return SourceCatalog.model_validate(yaml.safe_load(source))
