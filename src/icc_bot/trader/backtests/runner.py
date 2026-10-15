"""Run backtesters as subprocesses and return their output. Ported from Drive."""
from __future__ import annotations

import os
import subprocess

from .. import config


def list_backtests():
    return [{"name": n, "cmd": " ".join(c)} for n, c in config.BACKTESTS.items()]


def run(name: str, extra_args=None, timeout: int = 300):
    if name not in config.BACKTESTS:
        raise ValueError(f"unknown backtest {name}")
    cmd = list(config.BACKTESTS[name]) + list(extra_args or [])
    try:
        p = subprocess.run(cmd, cwd=config.REPO_ROOT, capture_output=True,
                           text=True, timeout=timeout, env=os.environ.copy())
        return {"name": name, "cmd": " ".join(cmd),
                "output": (p.stdout or "") + (p.stderr or ""), "code": p.returncode}
    except subprocess.TimeoutExpired:
        return {"name": name, "cmd": " ".join(cmd),
                "output": f"(timed out after {timeout}s)", "code": -1}
    except FileNotFoundError as e:
        return {"name": name, "cmd": " ".join(cmd),
                "output": f"(missing file: {e})", "code": -1}
