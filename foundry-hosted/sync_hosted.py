"""Copy the shared orchestration code into the hosted-agent folder (the deploy package must be self-contained).

Run before `azd deploy`:  python foundry-hosted/sync_hosted.py
"""
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = Path(__file__).resolve().parent / "src" / "risk-orchestrator"
PKG = DEST / "mbpoc"

FILES = ["__init__.py", "config.py", "contracts.py", "events.py", "policy.py",
         "agents/__init__.py", "agents/definitions.py", "agents/foundry.py", "agents/jsonutil.py",
         "agents/risk.py", "agents/evaluator.py", "agents/servicenow.py", "agents/mock_kb.py"]

if PKG.exists():
    shutil.rmtree(PKG)
for rel in FILES:
    target = PKG / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "src" / "mbpoc" / rel, target)
(DEST / "config").mkdir(exist_ok=True)
shutil.copy2(ROOT / "config" / "risk_criteria.yaml", DEST / "config" / "risk_criteria.yaml")
print(f"synced {len(FILES)} modules + risk_criteria.yaml -> {DEST}")
