"""Setup must not overwrite existing customer resources without opt-in."""
import importlib.util
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests
from azure.ai.projects.models import MCPTool, PromptAgentDefinition
from azure.core.exceptions import HttpResponseError, ResourceNotFoundError

from mbpoc.config import get_settings
from mbpoc.infra import foundry_agents, search_admin, sp_sync


@pytest.fixture
def settings():
    return replace(
        get_settings(), project_endpoint="https://example.services.ai.azure.com/api/projects/p",
        project_resource_id="/subscriptions/sub/resourceGroups/rg/providers/Microsoft.CognitiveServices/accounts/a/projects/p",
        search_endpoint="https://example.search.windows.net", kb_name="customer-kb", kb_connection="customer-mcp",
    )


def agent(name, settings):
    tool = MCPTool(
        server_label="knowledge-base", server_url=settings.mcp_endpoint,
        project_connection_id=settings.kb_connection, allowed_tools=["knowledge_base_retrieve"],
        require_approval="never",
    )
    other_tool = MCPTool(server_label="other", server_url="https://example.test/mcp")
    definition = PromptAgentDefinition(model="customer-model", instructions="Keep customer instructions", tools=[tool, other_tool], temperature=0.2)
    version = SimpleNamespace(version="7", definition=definition, metadata={"owner": "customer"}, description="Customer agent")
    return SimpleNamespace(name=name, versions=SimpleNamespace(latest=version))


@pytest.fixture
def sdk(monkeypatch, settings):
    client = Mock()
    client.agents.get.side_effect = lambda agent_name: agent(agent_name, settings)
    client.agents.create_version.side_effect = lambda agent_name, **kwargs: SimpleNamespace(name=agent_name, version="8")
    monkeypatch.setattr(foundry_agents, "AIProjectClient", Mock(return_value=client))
    return client


def test_repeated_setup_reuses_all_agents_without_versions_or_deletion(settings, sdk):
    foundry_agents.create_agents(settings)
    foundry_agents.create_agents(settings)
    sdk.agents.create_version.assert_not_called()
    sdk.agents.delete.assert_not_called()


def test_only_missing_agent_is_created(settings, sdk):
    def get(agent_name):
        if agent_name == settings.agent_evaluator:
            raise ResourceNotFoundError("missing")
        return agent(agent_name, settings)
    sdk.agents.get.side_effect = get
    foundry_agents.create_agents(settings)
    assert sdk.agents.create_version.call_count == 1
    assert sdk.agents.create_version.call_args.kwargs["agent_name"] == settings.agent_evaluator
    sdk.agents.delete.assert_not_called()


def test_explicit_update_publishes_versions_without_deleting(settings, sdk):
    foundry_agents.create_agents(settings, update_existing=True)
    assert sdk.agents.create_version.call_count == 4
    sdk.agents.delete.assert_not_called()


def test_agent_permission_error_never_triggers_creation(settings, sdk):
    sdk.agents.get.side_effect = HttpResponseError("Forbidden")
    with pytest.raises(HttpResponseError):
        foundry_agents.create_agents(settings)
    sdk.agents.create_version.assert_not_called()


def test_mismatched_risk_binding_is_not_silently_reused(settings, sdk):
    with pytest.raises(ValueError, match="switch explicitly"):
        foundry_agents.create_agents(replace(settings, kb_name="other-kb"))
    sdk.agents.create_version.assert_not_called()


def test_switch_changes_only_kb_tool_and_is_idempotent(settings, sdk):
    original = agent(settings.agent_risk, settings)
    sdk.agents.get.return_value = original
    sdk.agents.get.side_effect = None
    selected = replace(settings, kb_name="other-kb", kb_connection="other-mcp")
    foundry_agents.switch_knowledge_base(selected)
    kwargs = sdk.agents.create_version.call_args.kwargs
    definition = kwargs["definition"]
    assert definition.instructions == "Keep customer instructions"
    assert definition.model == "customer-model" and definition.temperature == 0.2
    assert definition.tools[1] == original.versions.latest.definition.tools[1]
    assert definition.tools[0].server_url == selected.mcp_endpoint
    assert definition.tools[0].project_connection_id == "other-mcp"
    assert original.versions.latest.definition.tools[0].server_url == settings.mcp_endpoint
    assert kwargs["metadata"] == {"owner": "customer"} and kwargs["description"] == "Customer agent"
    original.versions.latest.definition = definition
    foundry_agents.switch_knowledge_base(selected)
    assert sdk.agents.create_version.call_count == 1
    sdk.agents.delete.assert_not_called()


def response(status, body=None):
    result = requests.Response()
    result.status_code = status
    result._content = b"error"
    result.json = Mock(return_value=body)
    return result


def test_hosted_role_setup_reuses_existing_assignment(monkeypatch):
    spec = importlib.util.spec_from_file_location("deploy_hosted", Path(__file__).resolve().parents[1] / "scripts" / "deploy_hosted.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cli = Mock(return_value=["existing-assignment"])
    monkeypatch.setattr(module, "az", cli)
    module.ensure_role("principal", "Search Index Data Reader", "/search/service")
    cli.assert_called_once()
    assert cli.call_args.args[0][:3] == ["role", "assignment", "list"]


def test_hosted_role_setup_creates_only_missing_assignment(monkeypatch):
    spec = importlib.util.spec_from_file_location("deploy_hosted_missing", Path(__file__).resolve().parents[1] / "scripts" / "deploy_hosted.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cli = Mock(side_effect=[[], {"id": "created"}])
    monkeypatch.setattr(module, "az", cli)
    module.ensure_role("principal", "Foundry User", "/foundry/project")
    create = cli.call_args_list[1].args[0]
    assert create[:3] == ["role", "assignment", "create"]
    assert create[create.index("--assignee-object-id") + 1] == "principal"
    assert create[create.index("--assignee-principal-type") + 1] == "ServicePrincipal"
    assert create[create.index("--scope") + 1] == "/foundry/project"


@pytest.fixture
def rest(monkeypatch):
    get = Mock()
    put = Mock(return_value=response(200, {}))
    monkeypatch.setattr(search_admin.requests, "get", get)
    monkeypatch.setattr(search_admin.requests, "put", put)
    monkeypatch.setattr(search_admin, "_search_headers", lambda: {})
    monkeypatch.setattr(search_admin, "_cred", SimpleNamespace(get_token=lambda scope: SimpleNamespace(token="test-token")))
    return get, put


def test_existing_kb_and_sources_are_untouched(settings, rest):
    get, put = rest
    get.return_value = response(200, {"knowledgeSources": [{"name": "customer-source"}]})
    assert search_admin.setup_knowledge_base(settings) == ["customer-source"]
    assert get.call_count == 1
    put.assert_not_called()


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_search_errors_are_not_mistaken_for_missing_resources(settings, rest, status):
    get, put = rest
    get.return_value = response(status)
    with pytest.raises(requests.HTTPError):
        search_admin.setup_knowledge_base(settings)
    put.assert_not_called()


def test_existing_source_is_reused_while_creating_missing_kb(settings, rest):
    get, put = rest
    def read(url, **kwargs):
        if "/knowledgebases/" in url:
            return response(404)
        return response(200, {"name": "existing"})
    get.side_effect = read
    search_admin.setup_knowledge_base(settings)
    assert put.call_count == 1
    assert "/knowledgebases/customer-kb?" in put.call_args.args[0]


@pytest.mark.parametrize("connection_kind", ["mcp", "search"])
def test_existing_connection_is_reused_unchanged(settings, rest, connection_kind):
    get, put = rest
    target, category = (settings.mcp_endpoint, "RemoteTool") if connection_kind == "mcp" else (settings.search_endpoint, "CognitiveSearch")
    get.return_value = response(200, {"properties": {"target": target, "category": category, "authType": "CustomerAuth"}})
    ensure = search_admin.create_mcp_connection if connection_kind == "mcp" else search_admin.create_search_connection
    ensure(settings)
    ensure(settings)
    put.assert_not_called()


def test_connection_name_collision_fails_without_overwrite(settings, rest):
    get, put = rest
    get.return_value = response(200, {"properties": {"target": "https://different.test", "category": "RemoteTool"}})
    with pytest.raises(ValueError, match="different target"):
        search_admin.create_mcp_connection(settings)
    put.assert_not_called()


def test_missing_connection_is_created(settings, rest):
    get, put = rest
    get.return_value = response(404)
    search_admin.create_mcp_connection(settings)
    assert put.call_count == 1
    assert put.call_args.kwargs["json"]["properties"]["target"] == settings.mcp_endpoint


def test_connection_updates_require_opt_in(settings, rest):
    get, put = rest
    get.return_value = response(200, {"properties": {"target": "https://old.test"}})
    search_admin.create_mcp_connection(settings, update_existing=True)
    assert put.call_count == 1


@pytest.mark.parametrize("status", [403, 500])
def test_connection_lookup_errors_do_not_trigger_creation(settings, rest, status):
    get, put = rest
    get.return_value = response(status)
    with pytest.raises(requests.HTTPError):
        search_admin.create_mcp_connection(settings)
    put.assert_not_called()


def test_explicit_kb_update_applies_repository_definition(settings, rest):
    get, put = rest
    get.return_value = response(200, {"knowledgeSources": [{"name": "old-source"}]})
    search_admin.setup_knowledge_base(settings, update_existing=True)
    bodies = [call.kwargs["json"] for call in put.call_args_list]
    assert any(body["name"] == settings.kb_name for body in bodies)
    assert any(body["name"] == "mb-web-ks" for body in bodies)


@pytest.fixture
def setup_module(monkeypatch):
    monkeypatch.setattr("dotenv.load_dotenv", Mock())
    path = Path(__file__).resolve().parents[1] / "scripts" / "setup.py"
    spec = importlib.util.spec_from_file_location("guided_setup", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("existing_kb", [True, False])
def test_setup_does_not_sync_or_prune_customer_documents(monkeypatch, setup_module, existing_kb):
    monkeypatch.setattr(search_admin, "get_resource", Mock(side_effect=lambda s, kind, name: {"knowledgeSources": []} if existing_kb or kind == "indexes" else None))
    monkeypatch.setattr(search_admin, "setup_knowledge_base", Mock(return_value=[]))
    monkeypatch.setattr(search_admin, "create_mcp_connection", Mock())
    monkeypatch.setattr(search_admin, "create_search_connection", Mock())
    monkeypatch.setattr(foundry_agents, "create_agents", Mock())
    sync = Mock(side_effect=AssertionError("Existing content must not be synced or pruned"))
    monkeypatch.setattr(sp_sync, "sync", sync)
    setup_module.load_content_and_agents()
    sync.assert_not_called()


def test_switch_validates_kb_then_saves_selection(monkeypatch, settings, setup_module, tmp_path):
    import mbpoc.config
    env = tmp_path / ".env"
    env.write_text("KNOWLEDGE_BASE_NAME=customer-kb\n", encoding="utf-8")
    monkeypatch.setattr(setup_module, "ENV_FILE", env)
    get = Mock(return_value=settings)
    monkeypatch.setattr(mbpoc.config, "get_settings", get)
    monkeypatch.setattr(search_admin, "get_resource", Mock(return_value={"name": "other-kb"}))
    conn = Mock()
    bind = Mock()
    monkeypatch.setattr(search_admin, "create_mcp_connection", conn)
    monkeypatch.setattr(foundry_agents, "switch_knowledge_base", bind)
    setup_module.switch_knowledge_base("other-kb")
    assert conn.call_args.args[0].kb_connection == "kb-other-kb-mcp"
    assert bind.call_args.args[0].kb_name == "other-kb"
    assert "KNOWLEDGE_BASE_NAME=other-kb" in env.read_text(encoding="utf-8")


def test_missing_kb_does_not_change_connections_agents_or_env(monkeypatch, settings, setup_module, tmp_path):
    import mbpoc.config
    env = tmp_path / ".env"
    env.write_text("KNOWLEDGE_BASE_NAME=customer-kb\n", encoding="utf-8")
    monkeypatch.setattr(setup_module, "ENV_FILE", env)
    monkeypatch.setattr(mbpoc.config, "get_settings", Mock(return_value=settings))
    monkeypatch.setattr(search_admin, "get_resource", Mock(return_value=None))
    conn, bind = Mock(), Mock()
    monkeypatch.setattr(search_admin, "create_mcp_connection", conn)
    monkeypatch.setattr(foundry_agents, "switch_knowledge_base", bind)
    with pytest.raises(ValueError, match="not found"):
        setup_module.switch_knowledge_base("missing")
    conn.assert_not_called()
    bind.assert_not_called()
    assert env.read_text(encoding="utf-8") == "KNOWLEDGE_BASE_NAME=customer-kb\n"


def test_switch_binding_failure_leaves_saved_selection_unchanged(monkeypatch, settings, setup_module, tmp_path):
    import mbpoc.config
    env = tmp_path / ".env"
    env.write_text("KNOWLEDGE_BASE_NAME=customer-kb\n", encoding="utf-8")
    monkeypatch.setattr(setup_module, "ENV_FILE", env)
    monkeypatch.setattr(mbpoc.config, "get_settings", Mock(return_value=settings))
    monkeypatch.setattr(search_admin, "get_resource", Mock(return_value={"name": "other-kb"}))
    monkeypatch.setattr(search_admin, "create_mcp_connection", Mock())
    monkeypatch.setattr(foundry_agents, "switch_knowledge_base", Mock(side_effect=HttpResponseError("Forbidden")))
    with pytest.raises(HttpResponseError):
        setup_module.switch_knowledge_base("other-kb")
    assert env.read_text(encoding="utf-8") == "KNOWLEDGE_BASE_NAME=customer-kb\n"


def test_writing_selection_preserves_numbered_settings_without_duplicates(setup_module, tmp_path):
    env = tmp_path / ".env"
    setup_module.ENV_FILE = env
    setup_module.write_env({"KNOWLEDGE_BASE_NAME": "other-kb", "M365_TENANT_ID": "tenant", "A2A_API_KEY": "test-value"})
    lines = env.read_text(encoding="utf-8").splitlines()
    assert lines.count("M365_TENANT_ID=tenant") == 1
    assert lines.count("A2A_API_KEY=test-value") == 1
