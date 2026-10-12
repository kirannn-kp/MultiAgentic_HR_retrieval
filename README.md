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
It asks one question: **your Foundry project endpoint**. Find it in the Foundry portal: *your project → Overview → Project endpoint* (looks like `https://xxxx.services.ai.azure.com/api/projects/xxxx`). Everything else is found automatically. The setup then:
- gives the services the access rights they need,
- loads 9 sample HR / compliance documents,
- creates the Foundry IQ knowledge base (SharePoint documents + web),
- creates the four Foundry agents,
- checks that everything works.

You will see `OK` lines. A line marked `!!` or `PROBLEM` tells you what to fix (see *Troubleshooting*). The setup can be repeated safely.

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

The middle of the screen shows which agent is working, the activity log on the right shows every step and tool call, and the four tiles show *confidence*, *risk*, *answer quality* and *routing*.

### Optional: run the orchestrator as a Foundry hosted agent

This puts the orchestrator (an **Agent Framework workflow**) into Foundry itself, so you can show it in the portal.

```powershell
azd auth login --use-device-code      # once; follow the browser prompt
.\deploy-hosted.ps1                   # about 2 minutes
.\start.ps1                           # the demo now calls the hosted agent
```

### See it in the Foundry portal (https://ai.azure.com → your project)

| Where | What you see |
|---|---|
| **Build → Agents** | The four agents, and `risk-orchestrator` (the hosted one) with a **Playground** to chat with it directly. Always start a **new chat** after redeploying. |
| **Build → Knowledge** → connection dropdown → *your Search service* | The knowledge base `mb-enterprise-kb` with its two sources |
| Agent → **Traces** | Every step of a request |

## Use your own documents

1. Put your `.docx`, `.pdf`, `.md` or `.txt` files in a folder, for example `C:\MyPolicies`.
2. Run `.\.venv\Scripts\python scripts\sync_documents.py --folder C:\MyPolicies`.

The knowledge source mirrors the folder: removed files disappear from the answers. Rules for *risk* and the score limits are in `config\risk_criteria.yaml` (plain text; run `.\setup.ps1` again after changing it).

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
| First answer is slow (20-30 s) | Normal after the agent was idle |

## Remove everything the setup created

```powershell
.\.venv\Scripts\python scripts\cleanup.py
```
Removes the agents, knowledge base, index and connections. Your project, Search service and model deployment stay. Delete the hosted agent's azd environment folder `foundry-hosted\.azure` if you want a completely clean start.

## Connect your own MB Enterprise Chat (A2A)

The demo already talks to the orchestrator the way a real chat agent would: **A2A**. Any A2A client can call it:

- Agent card: `http://<gateway>/.well-known/agent-card.json`
- Endpoint: `POST http://<gateway>/a2a` with JSON-RPC `message/send` (`params.message.parts[0].text` = the question; optional `metadata.requester`)
- Result: an A2A task whose artifact has a text part and a data part (status, answer or ticket, scores, citations)

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
