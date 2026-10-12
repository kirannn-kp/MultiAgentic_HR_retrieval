"""Thin wrapper around the Azure CLI (works with az.cmd on Windows)."""
from __future__ import annotations

import json
import os
import subprocess
from typing import Any


class AzError(RuntimeError):
    pass


def az(args: list[str], *, json_output: bool = True, check: bool = True) -> Any:
    cmd = ["az", *args] + (["-o", "json"] if json_output and "-o" not in args and "--output" not in args else [])
    res = subprocess.run(subprocess.list2cmdline(cmd) if os.name == "nt" else cmd, capture_output=True, text=True, shell=(os.name == "nt"))
    if res.returncode:
        if not check:
            return None
        raise AzError(res.stderr.strip().splitlines()[-1] if res.stderr.strip() else f"az {' '.join(args[:3])} failed")
    out = res.stdout.strip()
    if json_output and out:
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            return out
    return out
