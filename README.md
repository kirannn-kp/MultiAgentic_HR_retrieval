# MB Enterprise Consultation Assistant

A working example of **AI agents that answer HR and compliance questions, check themselves, and hand risky cases to a human**. It runs on **Microsoft Foundry** (agents, Foundry IQ knowledge base, hosted agent built with Microsoft Agent Framework).

**What happens when someone asks a question**

1. The employee asks **MB Enterprise Chat**.
2. The **Risk Assessment Orchestrator** asks the **Risk Assessment Agent**, which searches the company knowledge (**SharePoint** documents and the **web**) through **Foundry IQ**, drafts an answer, and rates *confidence* and *risk*.
3. If confidence is low or the risk is **high** (for example dismissals, harassment, cross-border work) → a **human expert** gets a ticket (a ServiceNow stand-in; nothing real is created).
4. Otherwise an **Evaluator Agent** checks the answer quality. Good → the answer goes back to the employee, with sources. Not good → human expert.

Architecture and how it matches the target design: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

---

## Try it without Azure (2 minutes)

You only need Windows and **Python 3.10+**. This runs an offline simulation of the whole flow.

```powershell
.\start.ps1
```
The first start installs the components (a few minutes), then your browser opens the demo. Pick a **Test case** on the left and press **Run**. If PowerShell blocks the script, run this once: `Set-ExecutionPolicy -Scope Process Bypass`.

## Germany / European Union source routing (local POC)

Use **one knowledge base**, with a German web source, an EU web source and the shared internal-policy index. No extra agents or skills are needed.

The imported customer catalogue in [config/approved_web_sources.yaml](config/approved_web_sources.yaml) contains **53 URLs: 23 Germany and 30 European Union**. It contains no US group.

| Question jurisdiction | Eligible sources | Excluded sources |
|---|---|---|
| Germany | German + EU approved web sources, shared internal index | Old combined/unrestricted web source and any other attached source |
| European Union | EU approved web source, shared internal index | German source, old combined/unrestricted web source and any other attached source |

### Try regional routing without Azure

```powershell
.\start.ps1 -RegionalDemo
```

Select **Question jurisdiction**, then send a question. A missing selection produces a clarification, not a guessed country. The question travels unchanged; jurisdiction travels separately. The log shows allowed/excluded sources and URL counts.

**This is an offline demonstration:** it answers using fictional local policy documents and simulates the regional source plan. It does not fetch the approved websites or validate German/EU legal answers. The option forces local/offline settings for the launched processes only; it does not edit `.env` or change Azure resources.

### Maintain the approved website list

Edit the YAML catalogue, or import a revised Excel workbook:

```powershell
.\.venv\Scripts\python scripts\import_web_sources.py "C:\Customer\approved-sources.xlsx"
.\.venv\Scripts\python scripts\configure_regional.py
```

The workbook must have `Name`, `Region`, `Description`, `URL` columns; supported regions are Germany/DE and European Union/EU. Both commands are **local only** by default. Review the YAML before enabling live use.

`include_subpages: true` allows the approved domain/path and its descendants, not just one exact page. Set it to `false` for exact-path matching. HTTPS URLs cannot contain query strings, fragments or credentials. Restart after catalogue changes.

### Future live enablement (explicit Azure changes)

Do **not** run these steps just to try the offline demo. When approved to change Azure:

1. Use the existing KB selected by `KNOWLEDGE_BASE_NAME` and set `SEARCH_API_VERSION=2026-08-01-preview`.
2. Run `.\.venv\Scripts\python scripts\configure_regional.py --apply` to create missing regional web sources and attach them to that same KB. Matching existing sources are reused unchanged. Differing allowlists stop the command; `--apply --update-sources` is the explicit opt-in to replace the two regional definitions. Existing nonregional sources remain attached.
3. Set `REGIONAL_RETRIEVAL_ENABLED=true` in `.env`. If using Foundry hosted mode, redeploy the updated hosted orchestrator; redeploy an external gateway too, if used. Restart the local chat/gateway.
4. Run `.\.venv\Scripts\python scripts\check.py --smoke`, then validate real DE/EU searches and citations. Hosted Playground requests must be JSON, for example `{"question":"What requirements apply?","jurisdiction":"EU"}`.

If you later deliberately replace KB definitions with `setup.ps1 -UpdateExisting`, rerun the regional configuration command to restore its source attachments before using regional mode.

In live regional mode, code calls Foundry IQ REST with per-request `neverQuerySource` exclusions, verifies returned source activity and web URLs, then assesses only the supplied evidence using the existing risk prompt/model **with tools disabled**. It does not change the prompt-agent definition or switch KB bindings for each question. Missing sources, mismatched allowlists, partial responses or out-of-scope evidence are explicit errors; there is no unrestricted fallback. Risk, confidence and evaluator gates remain in place.

**Preview limitation:** `neverQuerySource` depends on `2026-08-01-preview`. Local tests use mocked service responses; live Azure compatibility and retrieval quality still require validation before customer/production use. The scope applies to this regional application flow, **not** direct calls to the risk prompt agent's existing MCP tool.

Microsoft documentation: [request-time source exclusion](https://learn.microsoft.com/azure/search/agentic-retrieval-how-to-retrieve) and [web source domain controls](https://learn.microsoft.com/azure/search/agentic-knowledge-source-how-to-web).

Regional hosted deployment also gives that existing agent identity `Search Service Contributor` and `Search Index Data Reader` on the selected Search service, plus `Foundry User` on the selected Foundry project. The first role is required to inspect KB/source definitions for fail-closed topology validation; it is broader than read-only because Azure currently has no built-in data-plane role limited to reading Search object definitions. The setup requires permission to create role assignments. Assignments are reused on later deployments and can take several minutes to propagate.

Jurisdiction means the law relevant to the question, not the employee's location. Region grouping alone does not establish legal applicability, topic relevance or German state applicability. The workbook's EU grouping is preserved, including organisations whose legal remit differs; customers must review it. Automatic jurisdiction/topic classification and US routing are not implemented.

## Run it for real on your Azure (about 15 minutes)

### What you need before you start

| Needed | Where to get it |
|---|---|
| A Windows PC with **Python 3.10+** | https://www.python.org/downloads (tick "Add python.exe to PATH") |
| The **Azure CLI** | https://aka.ms/installazurecliwindows |
| A **Microsoft Foundry project** with a **GPT model deployed** (we recommend `gpt-4.1-mini`) | https://ai.azure.com → create project → *Models + endpoints* → deploy `gpt-4.1-mini` |
| An **Azure AI Search** service, **Basic** tier or higher, in the same resource group | Azure portal → Create a resource → *Azure AI Search* |
| The **Owner** role on that resource group (to grant access rights) | Ask your Azure administrator |

### Steps

**1. Open PowerShell in this folder** (File Explorer → open the folder → click the address bar → type `powershell` → Enter).

**2. Sign in to Azure**
```powershell
az login
```

**3. Run the guided setup**
```powershell
.\setup.ps1
```
It asks for **your Foundry project endpoint** and lets you select a model or Search service if there are several. Find the endpoint in the Foundry portal: *your project → Overview → Project endpoint* (looks like `https://xxxx.services.ai.azure.com/api/projects/xxxx`). The setup then:
- gives the services the access rights they need,
- loads 9 sample HR / compliance documents only if neither the knowledge base nor the sample index exists,
- creates missing knowledge resources and project connections,
- creates missing Foundry prompt agents and reuses existing ones unchanged,
- checks that everything works.

You will see `OK` and `REUSED` lines. A line marked `!!` or `PROBLEM` tells you what to fix (see *Troubleshooting*). Repeating setup does not delete agents, publish new prompt-agent versions, overwrite existing connections or knowledge definitions, or reload existing documents. Access-right configuration is still checked/applied.

**4. Start the demo**
```powershell
.\start.ps1
```
The browser opens http://127.0.0.1:8000.

**5. Ask questions.** Use the *Test case* list, or type your own. Good ones to start with:

| Question | What you should see |
|---|---|
| What is the maximum value of a gift I can accept from a business partner? | **Answered**, with the source document |
| How many vacation days do I get per year and when do carried-over days expire? | **Answered** |
| What is the statutory minimum paid annual leave in Germany for a 5-day week? | **Answered** from the web (lower trust) |
| What severance applies when we terminate an employee? | **Escalated** to HR Legal (high risk) with a ticket number |
| Can I work from Spain for two months? | **Escalated** (cross-border = high risk) |
| Is there a sabbatical program or sponsored MBA study leave? | **Escalated** (not in the documents → low confidence) |
| help | MB Chat asks a **clarifying question** |

The middle of the screen shows agent activity, the activity log on the right shows steps, tool calls and durations, and the four tiles show *confidence*, *risk*, *answer quality* and *routing*.

### Faster replies and timing

The MB Enterprise Chat stand-in forwards the **original question unchanged** and formats the approved result without calling a model for intake or presentation. A simple local check still asks for clarification when the message has no identifiable topic, such as "help". The `mb-enterprise-chat-agent` prompt agent is retained for experimentation, but the demo no longer invokes it.

The chat immediately displays a receipt and elapsed time. **This is not the final answer**: real Foundry retrieval, risk assessment and quality evaluation still need to finish. There is no guaranteed instant answer. High-risk and insufficient-confidence cases still escalate; these checks are not bypassed for speed.

In hosted mode, the UI shows that it is waiting for Foundry. Internal workflow events become available **after completion**, not live, and are displayed without artificial replay delays. Timing entries show prompt-agent duration, orchestrator round-trip time and total chat time. The chat API also returns `timings_ms`; hosted internal timing entries require redeploying the updated hosted code.

### Optional: run the orchestrator as a Foundry hosted agent

This puts the orchestrator (an **Agent Framework workflow**) into Foundry itself, so you can show it in the portal.

```powershell
azd auth login --use-device-code      # once; follow the browser prompt
.\deploy-hosted.ps1                   # about 2 minutes
.\start.ps1                           # the demo now calls the hosted agent
```

Repeating `deploy-hosted.ps1` publishes a new version of the same hosted agent; it does not run prompt-agent/connection setup or create differently named duplicates. A new hosted version is a deployment revision, not another agent.

### See it in the Foundry portal (https://ai.azure.com → your project)

| Where | What you see |
|---|---|
| **Build → Agents** | The four agents, and `risk-orchestrator` (the hosted one) with a **Playground** to chat with it directly. Always start a **new chat** after redeploying. |
| **Build → Knowledge** → connection dropdown → *your Search service* | The knowledge base `mb-enterprise-kb` with its two sources |
| Agent → **Traces** | Every step of a request |

The **Indexes** tab is not the same listing as the knowledge bases on your Search service. An empty tab alone is not a reason to recreate a working knowledge base. Use the knowledge-base listing and `scripts\check.py` to verify the configured resources.

## Reuse resources, update deliberately, or switch knowledge bases

**Normal repeat setup:**
```powershell
.\setup.ps1
```
Existing agents, connections, knowledge sources and knowledge bases are reused unchanged. Only missing resources are created. If a connection name points somewhere else, or the risk agent uses a different KB binding, setup stops with an explanation rather than overwriting it.

**Apply repository definitions deliberately:**
```powershell
.\setup.ps1 -UpdateExisting
```
This explicitly replaces existing prompt-agent instructions/tools and knowledge/connection definitions with the repository settings. Agents receive new versions under the same names and are never deleted first. Existing documents are not reloaded. Use this option only after saving customer Web-UI edits in the repository; knowledge sources can be shared by other applications.

**Switch to an existing knowledge base by name:**
```powershell
.\setup.ps1 -KnowledgeBase "customer-hr-kb"
```
The knowledge base must already exist on the Search service configured in `.env`. This command:
1. Checks that the selected knowledge base exists; it does not create or overwrite its sources or documents.
2. Reuses or creates a dedicated MCP connection (`kb-customer-hr-kb-mcp` in this example).
3. Publishes a risk-agent version changing **only its KB tool URL and connection**, preserving its model, instructions, metadata and other tools. If the binding already matches, no new version is published.
4. Saves the selection in `.env`.

To use a particular existing MCP connection, provide its name:
```powershell
.\setup.ps1 -KnowledgeBase "customer-hr-kb" -KBConnection "customer-existing-mcp"
```
The connection must target the selected knowledge base. A conflicting connection is not overwritten. This explicitly changes the selected risk agent's binding for **all callers of that agent**, so use a separate risk-agent name/project if other applications must keep the old binding.

After switching, restart the demo. If `ORCHESTRATOR_MODE=foundry`, first run `.\deploy-hosted.ps1` to refresh the hosted settings, then restart with `.\start.ps1`. Do not just edit `KNOWLEDGE_BASE_NAME`: the risk agent's tool binding must also be updated, which the switch command handles. Switching across Search services or Foundry projects requires configuring those endpoints and their access rights separately.

## Use your own documents

1. Put your `.docx`, `.pdf`, `.md` or `.txt` files in a folder, for example `C:\MyPolicies`.
2. Run `.\.venv\Scripts\python scripts\sync_documents.py --folder C:\MyPolicies`.

The sample document index mirrors the folder: removed files disappear from that index. This command updates the POC's `mb-sharepoint-docs` index; it does not automatically ingest into an arbitrary customer knowledge base selected with the switch command.

Rules for *risk* and the score limits are in `config\risk_criteria.yaml` (plain text). After changing them, run `.\setup.ps1 -UpdateExisting` to publish the prompt changes. If using the hosted orchestrator, also run `.\deploy-hosted.ps1` so the hosted routing configuration stays aligned.

## Troubleshooting

| Message / problem | What to do |
|---|---|
| `You are not signed in to Azure` | Run `az login`, then repeat the step |
| `Could not find the Foundry resource` | You are in the wrong Azure subscription: `az account set --subscription "<name>"` |
| `No GPT model deployment found` | Deploy `gpt-4.1-mini` in the Foundry portal (*Models + endpoints*) |
| A line marked `!!` in step 2 | Your account cannot grant access rights. Ask an Azure administrator (Owner) to assign the roles named in the line, then run `.\setup.ps1` again |
| "Waiting for the new access rights to take effect" | Normal; Azure needs up to 5 minutes. The setup waits and continues |
| A question returns an error in the demo | Run `.\.venv\Scripts\python scripts\check.py --smoke` and read the `PROBLEM` line |
| Foundry playground shows a *checkpoint* error | You are in an old chat. Start a **new chat** and select the latest agent version |
| `deploy-hosted.ps1` says not signed in | Run `azd auth login --use-device-code` |
| An answer is slow | Check the timing entries in the activity log. Hosted internal stages appear after completion; the waiting message does not mean risk assessment has not started. Share the question, run id and timings when reporting a slow run |
| Existing risk agent uses a different knowledge base | Use `.\setup.ps1 -KnowledgeBase "<name>"` (and optionally `-KBConnection "<name>"`) to switch explicitly |

## Remove configured POC resources

**Warning:** cleanup targets the configured names, including reused resources. Do not run it against customer-owned/shared agents, connections or knowledge bases. Review the targets first and remove only resources you own.

```powershell
.\.venv\Scripts\python scripts\cleanup.py
```
Removes the agents, knowledge base, index and connections. Your project, Search service and model deployment stay. Delete the hosted agent's azd environment folder `foundry-hosted\.azure` if you want a completely clean start.

## Connect your own MB Enterprise Chat (A2A)

The demo already talks to the orchestrator the way a real chat agent would: **A2A**. Any A2A client can call it:

- Agent card: `http://<gateway>/.well-known/agent-card.json`
- Endpoint: `POST http://<gateway>/a2a` with JSON-RPC `message/send` (`params.message.parts[0].text` = the question; optional `metadata.requester`)
- Result: an A2A task whose artifact has a text part and a data part (status, answer or ticket, scores, citations)

When regional retrieval is enabled, include `params.message.metadata.jurisdiction` as `DE` or `EU`. Missing/unsupported values are rejected before retrieval. Keep the question text unchanged. The result's `risk.retrieval` records allowed, excluded and queried sources; `mock: true` denotes simulation.

With `ORCHESTRATOR_MODE=foundry` the gateway forwards to the Foundry hosted agent (Foundry does not offer A2A for hosted agents itself).

**Publish the gateway so a chat agent elsewhere (for example on AWS) can reach it** (optional, after `deploy-hosted.ps1`):

```powershell
.\deploy-gateway.ps1        # 10-15 minutes: Azure App Service (Linux B1), managed identity, API key
```
It prints the A2A endpoint, the agent card address and an **API key** (also saved in `.env` as `A2A_GATEWAY_KEY`). Give those two to the team that owns the chat agent; they send `Authorization: Bearer <key>`. Everything except the agent card and `/health` requires the key. The key is a simple stand-in: for production use Entra / OAuth sign-in. The App Service plan is billed per hour; `scripts/cleanup.py` removes it.

## Good to know

- All HR documents are **fictional samples**. The ServiceNow part only **acknowledges** a ticket; nothing is sent anywhere.
- The Foundry IQ knowledge base features are **preview**; names in the portal may change.
- The demo web page has **no sign-in**; run it only on your own computer.
- Costs: model calls, the Search service (Basic tier is billed per hour) and web search (billed by Microsoft). Remove the Search service when you no longer need it.

## For developers

`src/mbpoc` (agents, policy, orchestrator, UI) · `foundry-hosted` (Agent Framework workflow, deployed with `azd`) · `config/risk_criteria.yaml` · `tests` (`pytest`, offline). Details: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Optional extras for other setups (real SharePoint site creation, ERD documents in Blob storage): `scripts/optional/`. To hand the project to someone else: `.\package.ps1` creates `dist\mb-consultation-assistant.zip` without your settings or Azure state.

## Glossary

- **Foundry** – Microsoft's platform to build, host and manage AI agents.
- **Foundry IQ / knowledge base** – a managed search layer over your documents and the web that agents use to find answers with sources.
- **Hosted agent** – an agent whose code runs inside Foundry.
- **Agent Framework** – Microsoft's open-source library for building agents and multi-step workflows.
- **A2A** – a standard way for one agent to call another.
- **Confidence / risk / quality** – scores that decide whether the AI may answer or a human must.
