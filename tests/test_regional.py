"""Regional routing boundaries and request plumbing, without Azure access."""
import copy
import importlib.util
import json
import sys
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from mbpoc import mb_chat_api, orchestrator_api, regional_retrieval, workflow
from mbpoc.agents import foundry, hosted, mb_chat, risk
from mbpoc.config import ROOT, get_settings
from mbpoc.contracts import Citation, OrchestratorResponse, RiskResult
from mbpoc.infra import search_admin
from mbpoc.events import Tracer
from mbpoc.regional import ApprovedSource, load_catalog

QUESTION = "  What does German employment law require?\nKeep my wording.  "


@pytest.fixture
def regional_settings(monkeypatch):
    monkeypatch.setenv("MOCK_LATENCY_SEC", "0")
    settings = replace(get_settings(), mock=True, regional_retrieval=True,
                       search_api_version=regional_retrieval.API_VERSION)
    monkeypatch.setattr(mb_chat_api, "settings", settings)
    monkeypatch.setattr(orchestrator_api, "settings", settings)
    monkeypatch.setattr(risk, "get_settings", lambda: settings)
    monkeypatch.setattr(workflow, "get_settings", lambda: settings)
    return settings


def topology():
    result = {item["name"]: item for item in load_catalog().web_definitions()}
    result.update({"mb-sharepoint-ks": {"kind": "searchIndex"}, "mb-web-ks": {"kind": "web"},
                   "other-customer-source": {"kind": "web"}})
    return result


def answer(jurisdiction="DE"):
    return OrchestratorResponse(
        run_id="test", status="ANSWERED", answer="Test answer",
        risk=RiskResult(retrieval=load_catalog().plan(jurisdiction, topology())),
    )


def test_catalogue_preserves_customer_counts():
    catalogue = load_catalog()
    assert len(catalogue.sources) == 53
    assert len(catalogue.group("DE")) == 23
    assert len(catalogue.group("EU")) == 30
    assert len({source.url for source in catalogue.sources}) == 53


@pytest.mark.parametrize("region,count,excluded", [
    ("DE", 53, {"mb-web-ks", "other-customer-source"}),
    ("EU", 30, {"mb-web-de-ks", "mb-web-ks", "other-customer-source"}),
])
def test_single_kb_region_plan(region, count, excluded):
    scope = load_catalog().plan(region, topology())
    assert scope.approved_url_count == count
    assert set(scope.excluded_sources) == excluded
    assert "mb-sharepoint-ks" in scope.allowed_sources
    assert "mb-web-eu-ks" in scope.allowed_sources
    assert ("mb-web-de-ks" in scope.allowed_sources) == (region == "DE")


@pytest.mark.parametrize("mutation", ["missing", "unrestricted", "widened", "shared-web"])
def test_plan_fails_closed_on_wrong_source_configuration(mutation):
    attached = topology()
    if mutation == "missing":
        del attached["mb-web-eu-ks"]
    elif mutation == "unrestricted":
        attached["mb-web-eu-ks"]["webParameters"]["domains"]["allowedDomains"] = []
    elif mutation == "widened":
        attached["mb-web-eu-ks"]["webParameters"]["domains"]["allowedDomains"].append(
            {"address": "unapproved.test", "includeSubpages": True})
    else:
        attached["mb-sharepoint-ks"]["kind"] = "web"
    with pytest.raises(ValueError):
        load_catalog().plan("EU", attached)


@pytest.mark.parametrize("region", [None, "US", "Germany", ""])
def test_no_silent_country_guess(region):
    with pytest.raises(ValueError, match="Select"):
        load_catalog().plan(region, topology())


@pytest.mark.parametrize("url", [
    "http://example.test", "https://user:password@example.test", "https://example.test:443",
    "https://example.test/path?query=value", "https://example.test/a/../b",
    "https://example.test/a/%2e%2e/b", "https://example.test\\bad", "https://example.test /bad",
])
def test_catalogue_rejects_invalid_addresses(url):
    with pytest.raises(ValueError):
        ApprovedSource(name="Test", region="DE", description="Test source", url=url)


@pytest.mark.parametrize("url,permitted", [
    ("https://approved.test/law", True), ("https://approved.test/law/chapter?q=test", True),
    ("https://approved.test/lawyer", False), ("https://approved.test.evil/law", False),
    ("https://other.test/law", False), ("http://approved.test/law", False),
    ("https://approved.test/law/%2e%2e/other", False),
    ("https://approved.test/law/../other", False), ("https://approved.test:bad/law", False),
])
def test_citation_domain_and_path_controls(url, permitted):
    source = ApprovedSource(name="Test", region="EU", description="Test source", url="https://approved.test/law")
    assert source.permits(url) is permitted


def test_every_source_is_explicitly_scoped_and_question_unchanged():
    attached = topology()
    scope = load_catalog().plan("EU", attached)
    request = regional_retrieval.retrieval_request(QUESTION, scope, attached)
    assert request["messages"][0]["content"][0]["text"] == QUESTION
    assert request["outputMode"] == "extractiveData"
    params = {entry["knowledgeSourceName"]: entry for entry in request["knowledgeSourceParams"]}
    assert params.keys() == attached.keys()
    for name, entry in params.items():
        assert entry["neverQuerySource"] == (name in scope.excluded_sources)
        if name in scope.allowed_sources:
            assert entry["alwaysQuerySource"] and entry["failOnError"]
            assert entry["includeReferences"] and entry["includeReferenceSourceData"]


def response_body():
    url = load_catalog().group("EU")[0].url
    return {
        "activity": [{"id": 1, "knowledgeSourceName": "mb-web-eu-ks"}],
        "references": [{"id": "0", "type": "web", "activitySource": 1, "url": url, "title": "EU source"}],
        "response": [{"content": [{"type": "text", "text": json.dumps([
            {"ref_id": "0", "title": "EU source", "content": "Retrieved legal evidence"}
        ])}]}],
    }


def test_web_response_has_actual_evidence_and_queried_scope():
    evidence = regional_retrieval.parse_evidence(response_body(), load_catalog().plan("EU", topology()), load_catalog())
    assert evidence.citations[0].source == "web"
    assert evidence.citations[0].url == load_catalog().group("EU")[0].url
    assert evidence.citations[0].snippet == "Retrieved legal evidence"
    assert evidence.scope.queried_sources == ["mb-web-eu-ks"]


def test_index_reference_parses_actual_source_url():
    body = response_body()
    body["activity"][0]["knowledgeSourceName"] = "mb-sharepoint-ks"
    body["references"][0] = {"id": "0", "type": "searchIndex", "activitySource": 1,
                             "sourceData": {"source_url": "https://sharepoint.test/policy"}}
    evidence = regional_retrieval.parse_evidence(body, load_catalog().plan("EU", topology()), load_catalog())
    assert evidence.citations[0].source == "sharepoint"
    assert evidence.citations[0].url == "https://sharepoint.test/policy"


@pytest.mark.parametrize("mutation", ["excluded", "unknown", "outside-url", "error", "wrong-type", "empty-content", "missing-reference"])
def test_bad_evidence_is_not_forwarded(mutation):
    body = response_body()
    if mutation in ("excluded", "unknown"):
        body["activity"][0]["knowledgeSourceName"] = "mb-web-de-ks" if mutation == "excluded" else "unknown"
    elif mutation == "outside-url":
        body["references"][0]["url"] = "https://unapproved.test"
    elif mutation == "error":
        body["activity"][0]["error"] = {"message": "Source unavailable"}
    elif mutation == "wrong-type":
        body["references"][0]["type"] = "searchIndex"
    elif mutation == "empty-content":
        body["response"][0]["content"][0]["text"] = '[{"ref_id":"0","content":""}]'
    else:
        body["references"] = []
    with pytest.raises((ValueError, KeyError)):
        regional_retrieval.parse_evidence(body, load_catalog().plan("EU", topology()), load_catalog())


@pytest.mark.parametrize("status", [206, 403, 500])
def test_http_failure_never_falls_back(monkeypatch, regional_settings, status):
    import requests
    monkeypatch.setattr(regional_retrieval, "attached_sources", lambda _: topology())
    monkeypatch.setattr(regional_retrieval, "_credential", SimpleNamespace(
        get_token=lambda _: SimpleNamespace(token="offline-test-token")))
    response = Mock(status_code=status)
    if status >= 400:
        response.raise_for_status.side_effect = requests.HTTPError("Test retrieval failure")
    monkeypatch.setattr(regional_retrieval.requests, "post", Mock(return_value=response))
    with pytest.raises((ValueError, requests.HTTPError)):
        regional_retrieval.retrieve(QUESTION, "EU", regional_settings, Tracer("test", lambda _: None))
    response.json.assert_not_called()


def test_nonpreview_api_fails_before_auth(regional_settings):
    with pytest.raises(ValueError, match="preview"):
        regional_retrieval.require_preview(replace(regional_settings, search_api_version="2025-11-01"))


def test_missing_region_clarifies_without_orchestrator_call(monkeypatch, regional_settings):
    call = Mock(side_effect=AssertionError("No retrieval before jurisdiction selection"))
    monkeypatch.setattr(mb_chat, "call_orchestrator", call)
    result = mb_chat_api._run(mb_chat_api.ChatRequest(message=QUESTION), Tracer("test", lambda _: None))
    assert result["status"] == "CLARIFY"
    assert "Germany or European Union" in result["reply"]
    call.assert_not_called()


def test_chat_passes_country_separately_without_rewording(monkeypatch, regional_settings):
    call = Mock(return_value=answer())
    monkeypatch.setattr(mb_chat, "call_orchestrator", call)
    result = mb_chat_api._run(mb_chat_api.ChatRequest(message=QUESTION, jurisdiction="DE"), Tracer("test", lambda _: None))
    assert result["status"] == "ANSWERED"
    assert call.call_args.args[0] == QUESTION
    assert call.call_args.args[3] == "DE"


def test_chat_rejects_unsupported_region():
    with pytest.raises(ValidationError):
        mb_chat_api.ChatRequest(message=QUESTION, jurisdiction="US")


@pytest.mark.parametrize("region", [None, "US", "Germany"])
def test_a2a_rejects_missing_or_unsupported_region(monkeypatch, regional_settings, region):
    monkeypatch.setattr(orchestrator_api, "API_KEY", "")
    call = Mock(side_effect=AssertionError("No invalid regional workflow"))
    monkeypatch.setattr(orchestrator_api, "handle_request", call)
    with TestClient(orchestrator_api.app) as client:
        result = client.post("/a2a", json={
            "jsonrpc": "2.0", "id": "test", "method": "message/send",
            "params": {"message": {"metadata": {"jurisdiction": region},
                                   "parts": [{"kind": "text", "text": QUESTION}]}},
        }).json()
    assert result["error"]["code"] == -32602
    call.assert_not_called()


@pytest.mark.parametrize("mode", ["local", "foundry"])
def test_a2a_carries_region_in_both_modes(monkeypatch, regional_settings, mode):
    monkeypatch.setattr(orchestrator_api, "API_KEY", "")
    monkeypatch.setenv("ORCHESTRATOR_MODE", mode)
    call = Mock(return_value=answer())
    monkeypatch.setattr(orchestrator_api, "handle_request", call)
    monkeypatch.setattr(hosted, "call", call)
    with TestClient(orchestrator_api.app) as client:
        result = client.post("/a2a", json={
            "jsonrpc": "2.0", "id": "test", "method": "message/send",
            "params": {"message": {"metadata": {"jurisdiction": "DE"},
                                   "parts": [{"kind": "text", "text": QUESTION}]}},
        }).json()
    assert "result" in result
    assert call.call_args.args[0] == QUESTION
    assert call.call_args.args[3] == "DE"


def test_hosted_payload_and_old_deployment_guard(monkeypatch):
    create = Mock(return_value=SimpleNamespace(output_text=answer().model_dump_json()))
    monkeypatch.setattr(hosted, "_openai_client", lambda: SimpleNamespace(responses=SimpleNamespace(create=create)))
    result = hosted.call(QUESTION, "tester", jurisdiction="DE")
    payload = json.loads(create.call_args.kwargs["input"])
    assert payload == {"question": QUESTION, "requester": "tester", "jurisdiction": "DE"}
    assert result.risk.retrieval.jurisdiction == "DE"
    old = answer()
    old.risk.retrieval = None
    create.return_value.output_text = old.model_dump_json()
    with pytest.raises(ValueError, match="Hosted deployment"):
        hosted.call(QUESTION, "tester", jurisdiction="DE")


def test_mock_scope_does_not_claim_website_fetches(regional_settings):
    events = []
    result = risk.assess("How many vacation days?", "tester", Tracer("test", events.append), "EU")
    assert result.retrieval.mock
    assert result.retrieval.queried_sources == ["mb-sharepoint-ks"]
    assert all(citation.source != "web" for citation in result.citations)
    assert any(event["kind"] == "retrieval_scope" for event in events)


def test_evidence_assessment_uses_existing_prompt_without_tools():
    from azure.ai.projects.models import PromptAgentDefinition
    runner = object.__new__(foundry.FoundryRunner)
    definition = PromptAgentDefinition(model="existing-customer-model", instructions="Customer risk criteria")
    get = Mock(return_value=SimpleNamespace(versions=SimpleNamespace(latest=SimpleNamespace(definition=definition))))
    runner._project = SimpleNamespace(agents=SimpleNamespace(get=get))
    create = Mock(return_value=SimpleNamespace(output=[], output_text="{}"))
    runner._openai = SimpleNamespace(responses=SimpleNamespace(create=create))
    runner.call_with_evidence("risk-agent", QUESTION, Tracer("test", lambda _: None))
    assert create.call_args.kwargs["tools"] == []
    assert create.call_args.kwargs["tool_choice"] == "none"
    assert create.call_args.kwargs["model"] == "existing-customer-model"
    assert "Customer risk criteria" in create.call_args.kwargs["instructions"]
    assert "extra_body" not in create.call_args.kwargs
    create.return_value.output = [SimpleNamespace(type="mcp_call")]
    with pytest.raises(ValueError, match="tool call"):
        runner.call_with_evidence("risk-agent", QUESTION, Tracer("test", lambda _: None))


def test_regional_configuration_default_is_local_only(monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location("configure_regional", ROOT / "scripts" / "configure_regional.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(sys, "argv", ["configure_regional.py"])
    monkeypatch.setattr(module, "get_settings", Mock(side_effect=AssertionError("No Azure settings needed")))
    module.main()
    assert "Local preview only. No Azure calls or changes." in capsys.readouterr().out


def script_module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_repeated_regional_apply_reuses_resources_and_preserves_kb(monkeypatch, regional_settings):
    module = script_module("configure_regional")
    original = {"name": regional_settings.kb_name, "retrievalInstructions": "Customer instructions",
                "knowledgeSources": [{"name": "mb-sharepoint-ks"}, {"name": "customer-extra-ks", "alwaysQuerySource": True}]}
    backend = {("knowledgebases", regional_settings.kb_name): copy.deepcopy(original)}
    puts = []

    def put(_, kind, name, body):
        puts.append((kind, name))
        backend[(kind, name)] = copy.deepcopy(body)

    monkeypatch.setattr(search_admin, "get_resource", lambda _, kind, name: copy.deepcopy(backend.get((kind, name))))
    monkeypatch.setattr(search_admin, "_put", put)
    monkeypatch.setattr(module, "get_settings", lambda: regional_settings)
    monkeypatch.setattr(sys, "argv", ["configure_regional.py", "--apply"])
    module.main()
    assert len(puts) == 3
    kb = backend[("knowledgebases", regional_settings.kb_name)]
    assert kb["retrievalInstructions"] == original["retrievalInstructions"]
    assert kb["knowledgeSources"][:2] == original["knowledgeSources"]
    assert len(kb["knowledgeSources"]) == 4
    for name in ("mb-web-de-ks", "mb-web-eu-ks"):
        backend[("knowledgesources", name)]["webParameters"]["domains"]["blockedDomains"] = []
        backend[("knowledgesources", name)]["webParameters"]["serverAddedDefault"] = None
    puts.clear()
    module.main()
    assert puts == []
    assert len(backend[("knowledgebases", regional_settings.kb_name)]["knowledgeSources"]) == 4


def test_regional_apply_checks_conflicts_before_creating_sources(monkeypatch, regional_settings):
    module = script_module("configure_regional")
    bad = {"kind": "web", "webParameters": {}}
    kb = {"knowledgeSources": []}
    monkeypatch.setattr(search_admin, "get_resource", lambda _, kind, name:
                        kb if kind == "knowledgebases" else bad if name == "mb-web-eu-ks" else None)
    put = Mock()
    monkeypatch.setattr(search_admin, "_put", put)
    monkeypatch.setattr(module, "get_settings", lambda: regional_settings)
    monkeypatch.setattr(sys, "argv", ["configure_regional.py", "--apply"])
    with pytest.raises(ValueError, match="update-sources"):
        module.main()
    put.assert_not_called()


@pytest.mark.parametrize("mutation", ["valid", "bad-header", "unsupported-region", "duplicate-url"])
def test_excel_import_is_validated_and_read_only(tmp_path, mutation):
    from openpyxl import Workbook
    module = script_module("import_web_sources")
    path = tmp_path / "sources.xlsx"
    book = Workbook()
    sheet = book.active
    sheet.append(["Name", "Region", "Description", "URL"])
    sheet.append(["German source", "Germany", "Description", "https://german.test"])
    sheet.append(["EU source", "European Union", "Description", "https://eu.test"])
    if mutation == "bad-header":
        sheet["D1"] = "Website"
    elif mutation == "unsupported-region":
        sheet["B2"] = "United States"
    elif mutation == "duplicate-url":
        sheet["D3"] = sheet["D2"].value
    book.save(path)
    book.close()
    original = path.read_bytes()
    if mutation == "valid":
        catalog = module.read_catalog(path)
        assert len(catalog.sources) == 2
        assert len(catalog.group("DE")) == len(catalog.group("EU")) == 1
    else:
        with pytest.raises(ValueError):
            module.read_catalog(path)
    assert path.read_bytes() == original


def test_mb_a2a_metadata_contains_separate_jurisdiction(monkeypatch):
    result = answer("EU")
    response = Mock()
    response.json.return_value = {"result": {"artifacts": [{"parts": [{"kind": "data", "data": result.model_dump()}]}]}}
    post = Mock(return_value=response)
    monkeypatch.setattr(mb_chat.httpx, "post", post)
    mb_chat.call_orchestrator(QUESTION, "tester", Tracer("test", lambda _: None), "EU")
    message = post.call_args.kwargs["json"]["params"]["message"]
    assert message["parts"][0]["text"] == QUESTION
    assert message["metadata"]["jurisdiction"] == "EU"


def test_chat_refuses_an_old_unscoped_gateway_response(monkeypatch):
    result = answer("EU")
    result.risk.retrieval = None
    response = Mock()
    response.json.return_value = {"result": {"artifacts": [{"parts": [{"kind": "data", "data": result.model_dump()}]}]}}
    monkeypatch.setattr(mb_chat.httpx, "post", Mock(return_value=response))
    with pytest.raises(ValueError, match="scoped retrieval"):
        mb_chat.call_orchestrator(QUESTION, "tester", Tracer("test", lambda _: None), "EU")


@pytest.mark.parametrize("mode", ["valid", "invented-citation", "missing-citation", "no-evidence"])
def test_live_risk_is_limited_to_supplied_evidence(monkeypatch, regional_settings, mode):
    settings = replace(regional_settings, mock=False)
    monkeypatch.setattr(risk, "get_settings", lambda: settings)
    evidence = regional_retrieval.parse_evidence(response_body(), load_catalog().plan("EU", topology()), load_catalog())
    citations = [citation.model_dump() for citation in evidence.citations]
    if mode == "invented-citation":
        citations[0]["url"] = "https://invented.test"
    elif mode in ("missing-citation", "no-evidence"):
        citations = []
    if mode == "no-evidence":
        evidence.documents = []
        evidence.citations = []
    monkeypatch.setattr(regional_retrieval, "retrieve", lambda *args: evidence)
    call = Mock(return_value=SimpleNamespace(text=json.dumps({
        "draft_answer": "Draft answer", "confidence": 0.95, "risk_level": "LOW", "citations": citations,
    })))
    monkeypatch.setattr(risk, "get_runner", lambda: SimpleNamespace(call_with_evidence=call))
    if mode in ("invented-citation", "missing-citation"):
        with pytest.raises(ValueError, match="cited|cite"):
            risk.assess(QUESTION, "tester", Tracer("test", lambda _: None), "EU")
    else:
        result = risk.assess(QUESTION, "tester", Tracer("test", lambda _: None), "EU")
        assert result.citations == evidence.citations
        assert result.retrieval.jurisdiction == "EU"
        assert result.confidence == (0.0 if mode == "no-evidence" else risk.WEB_ONLY_MAX_CONFIDENCE)
    payload = json.loads(call.call_args.args[1])
    assert payload["question"] == QUESTION and payload["jurisdiction"] == "EU"


def test_retrieval_rest_request_uses_scope_and_exact_question(monkeypatch, regional_settings):
    monkeypatch.setattr(regional_retrieval, "attached_sources", lambda _: topology())
    monkeypatch.setattr(regional_retrieval, "_credential", SimpleNamespace(
        get_token=lambda _: SimpleNamespace(token="offline-test-token")))
    response = Mock(status_code=200)
    response.json.return_value = response_body()
    post = Mock(return_value=response)
    monkeypatch.setattr(regional_retrieval.requests, "post", post)
    evidence = regional_retrieval.retrieve(QUESTION, "EU", regional_settings, Tracer("test", lambda _: None))
    assert evidence.scope.queried_sources == ["mb-web-eu-ks"]
    request = post.call_args.kwargs["json"]
    assert request["messages"][0]["content"][0]["text"] == QUESTION
    assert all(entry["neverQuerySource"] for entry in request["knowledgeSourceParams"]
               if entry["knowledgeSourceName"] in evidence.scope.excluded_sources)
