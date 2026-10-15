"""Start/stop strategy bots as subprocesses and capture their output.

Ported from the Drive cockpit, with one addition: a BOTS entry may be a bare
command list, or a dict {"cmd": [...], "env": {...}} so each ICC bot can carry
its own settings (symbol, venue, mode) as environment variables.
"""
from __future__ import annotations

import os
import subprocess

from .. import config

_PROCS: dict = {}   # name -> Popen
_LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_logs")
os.makedirs(_LOG_DIR, exist_ok=True)


def _logfile(name: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in name)
    return os.path.join(_LOG_DIR, f"{safe}.log")


def _spec(name: str):
    """Return (cmd_list, env_overrides) for a BOTS entry in either shape."""
    entry = config.BOTS[name]
    if isinstance(entry, dict):
        return list(entry.get("cmd", [])), dict(entry.get("env", {}))
    return list(entry), {}


def list_bots():
    out = []
    for name in config.BOTS:
        p = _PROCS.get(name)
        running = p is not None and p.poll() is None
        cmd, env = _spec(name)
        label = " ".join(cmd)
        if env:
            label += "  [" + " ".join(f"{k}={v}" for k, v in env.items()) + "]"
        out.append({"name": name, "running": running, "cmd": label})
    return out


def start(name: str):
    if name not in config.BOTS:
        raise ValueError(f"unknown bot {name}")
    p = _PROCS.get(name)
    if p is not None and p.poll() is None:
        return {"name": name, "running": True, "note": "already running"}
    cmd, env_over = _spec(name)
    env = os.environ.copy()
    env.update({k: str(v) for k, v in env_over.items()})
    logf = open(_logfile(name), "w")
    proc = subprocess.Popen(cmd, cwd=config.REPO_ROOT, stdout=logf,
                            stderr=subprocess.STDOUT, text=True, env=env)
    _PROCS[name] = proc
    return {"name": name, "running": True}


def stop(name: str):
    p = _PROCS.get(name)
    if p is not None and p.poll() is None:
        p.terminate()
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()
    return {"name": name, "running": False}


def log(name: str, tail: int = 200) -> str:
    path = _logfile(name)
    if not os.path.exists(path):
        return ""
    with open(path) as f:
        lines = f.readlines()
    return "".join(lines[-tail:])
