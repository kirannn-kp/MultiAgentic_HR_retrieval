"""Unit tests for the parts that only run against live Foundry (parsing + definitions), using fake data."""
import os

os.environ["MOCK_MODE"] = "true"

from types import SimpleNamespace

from mbpoc.agents import evaluator, risk
from mbpoc.agents.definitions import agent_specs, risk_instructions
from mbpoc.agents.foundry import _describe
from mbpoc.agents.jsonutil import extract_json
from mbpoc.config import get_settings


def test_extract_json_handles_fences_and_prose():
    assert extract_json('Sure!\n```json\n{"a": {"b": 1}}\n```\nthanks') == {"a": {"b": 1}}


def test_risk_parse_fails_safe_to_high():
    r = risk._parse('{"draft_answer": "x", "confidence": 1.7, "risk_level": "banana", "citations": [{"source": "erd", "title": "t", "extra": 1}]}')
    assert r.risk_level == "HIGH" and r.confidence == 1.0 and r.citations[0].source == "erd"


def test_evaluator_parse_defaults():
    q = evaluator._parse('{"score": 0.8, "groundedness": 0.9}')
    assert q.score == 0.8 and q.policy_compliance == 1.0


def test_mcp_call_is_described():
    item = SimpleNamespace(type="mcp_call", name="knowledge_base_retrieve", arguments='{"q":"x"}', output="mb-sharepoint-ks ...")
    assert _describe(item)["tool"] == "knowledge_base_retrieve"


def test_agent_specs_and_instructions():
    specs = agent_specs(get_settings())
    assert [s["tools"] for s in specs][0] == ["kb_mcp"]
    assert "employment-law" in risk_instructions() and "knowledge_base_retrieve" in risk_instructions()


def test_foundry_sdk_objects_construct():
    from azure.ai.projects.models import MCPTool, PromptAgentDefinition

    tool = MCPTool(server_label="knowledge-base", server_url="https://x.search.windows.net/knowledgebases/kb/mcp", require_approval="never",
                   allowed_tools=["knowledge_base_retrieve"], project_connection_id="conn")
    PromptAgentDefinition(model="gpt-4.1-mini", instructions="hi", tools=[tool])
