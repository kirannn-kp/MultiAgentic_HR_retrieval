"""Reuse Foundry prompt agents; publish changes only when explicitly requested."""
from __future__ import annotations

from copy import deepcopy

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import AgentDetails, MCPTool, PromptAgentDefinition
from azure.core.exceptions import ResourceNotFoundError
from azure.identity import DefaultAzureCredential

from ..agents.definitions import agent_specs
from ..config import Settings


def _get_agent(client: AIProjectClient, name: str) -> AgentDetails | None:
    try:
        return client.agents.get(agent_name=name)
    except ResourceNotFoundError:
        return None


def _kb_tool(definition: PromptAgentDefinition) -> MCPTool:
    tools = [
        tool for tool in definition.tools or []
        if isinstance(tool, MCPTool) and (
            tool.server_label == "knowledge-base"
            or isinstance(tool.allowed_tools, list) and "knowledge_base_retrieve" in tool.allowed_tools
        )
    ]
    if len(tools) != 1:
        raise ValueError("Risk agent must have exactly one identifiable knowledge-base MCP tool; check its tools in Foundry.")
    return tools[0]


def _matches_kb(tool: MCPTool, s: Settings) -> bool:
    return (
        tool.server_url.split("?", 1)[0].rstrip("/") == s.mcp_endpoint.split("?", 1)[0].rstrip("/")
        and tool.project_connection_id == s.kb_connection
    )


def create_agents(s: Settings, *, update_existing: bool = False) -> list[str]:
    client = AIProjectClient(endpoint=s.project_endpoint, credential=DefaultAzureCredential())
    created = []
    for spec in agent_specs(s):
        existing = _get_agent(client, spec["name"])
        if existing and not update_existing:
            if spec["name"] == s.agent_risk:
                definition = existing.versions.latest.definition
                if not isinstance(definition, PromptAgentDefinition) or not _matches_kb(_kb_tool(definition), s):
                    raise ValueError(
                        f"Existing risk agent '{s.agent_risk}' uses a different knowledge-base binding. "
                        "Use setup.ps1 -KnowledgeBase <name> [-KBConnection <name>] to switch explicitly; "
                        "no existing agent was overwritten."
                    )
            created.append(f"{existing.name} v{existing.versions.latest.version}")
            print(f"  REUSED {existing.name} (unchanged)")
            continue
        tools = []
        if "kb_mcp" in spec["tools"]:
            tools.append(MCPTool(
                server_label="knowledge-base", server_url=s.mcp_endpoint, require_approval="never",
                allowed_tools=["knowledge_base_retrieve"], project_connection_id=s.kb_connection,
            ))
        agent = client.agents.create_version(
            agent_name=spec["name"],
            definition=PromptAgentDefinition(model=s.model, instructions=spec["instructions"], tools=tools or None),
        )
        created.append(f"{agent.name} v{agent.version}")
        print(f"  OK {agent.name} (version {agent.version}) tools={[t.__class__.__name__ for t in tools] or 'none'}")
    return created


def switch_knowledge_base(s: Settings) -> None:
    """Change only the risk agent's KB tool; keep customer prompts, model and other tools."""
    client = AIProjectClient(endpoint=s.project_endpoint, credential=DefaultAzureCredential())
    existing = _get_agent(client, s.agent_risk)
    if existing is None:
        raise ValueError(f"Risk agent '{s.agent_risk}' was not found. Run setup.ps1 first.")
    version = existing.versions.latest
    definition = deepcopy(version.definition)
    if not isinstance(definition, PromptAgentDefinition):
        raise ValueError(f"Risk agent '{s.agent_risk}' must be a prompt agent.")
    tool = _kb_tool(definition)
    if _matches_kb(tool, s):
        print(f"  REUSED {s.agent_risk}: already bound to '{s.kb_name}'")
        return
    tool.server_url = s.mcp_endpoint
    tool.project_connection_id = s.kb_connection
    agent = client.agents.create_version(
        agent_name=s.agent_risk, definition=definition,
        metadata=version.metadata, description=version.description,
    )
    print(f"  UPDATED {agent.name} v{agent.version}: KB binding only; prompts and other tools preserved")
