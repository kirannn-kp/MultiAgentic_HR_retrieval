"""Start the orchestrator (8001) and the MB Enterprise Chat mock + UI (8000). Usage: python -m mbpoc.run_all"""
from __future__ import annotations

import subprocess
import sys
import time

from .config import get_settings


def main() -> None:
    s = get_settings()
    cmds = [
        [sys.executable, "-m", "uvicorn", "mbpoc.orchestrator_api:app", "--host", s.orch_host, "--port", str(s.orch_port)],
        [sys.executable, "-m", "uvicorn", "mbpoc.mb_chat_api:app", "--host", s.mb_host, "--port", str(s.mb_port)],
    ]
    procs = [subprocess.Popen(c) for c in cmds]
    print(f"\nMode: {'MOCK (offline)' if s.mock else 'LIVE Foundry'}\nUI:   http://{s.mb_host}:{s.mb_port}\nA2A:  {s.orchestrator_url}/a2a  (agent card: /.well-known/agent-card.json)\n")
    try:
        while all(p.poll() is None for p in procs):
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        for p in procs:
            if p.poll() is None:
                p.terminate()


if __name__ == "__main__":
    main()
