"""Create / update the Foundry prompt agents (new version each run)."""
from __future__ import annotations

from ..agents.definitions import agent_specs
from ..config import Settings


def create_agents(s: Settings) -> list[str]:
    from azure.ai.projects import AIProjectClient
    from azure.ai.projects.models import MCPTool, PromptAgentDefinition
    from azure.identity import DefaultAzureCredential

    client = AIProjectClient(endpoint=s.project_endpoint, credential=DefaultAzureCredential())
    created = []
    for spec in agent_specs(s):
        tools = []
        if "kb_mcp" in spec["tools"]:
            tools.append(MCPTool(
                server_label="knowledge-base", server_url=s.mcp_endpoint, require_approval="never",
                allowed_tools=["knowledge_base_retrieve"], project_connection_id=s.kb_connection,
            ))
        try:  # create_version did not roll the definition forward in testing, so replace the agent to make the script idempotent
            client.agents.delete(agent_name=spec["name"])
        except Exception:
            pass
        agent = client.agents.create_version(
            agent_name=spec["name"],
            definition=PromptAgentDefinition(model=s.model, instructions=spec["instructions"], tools=tools or None),
        )
        created.append(f"{agent.name} v{agent.version}")
        print(f"  OK {agent.name} (version {agent.version}) tools={[t.__class__.__name__ for t in tools] or 'none'}")
    return created
