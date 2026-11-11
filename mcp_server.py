"""MCP server: lets any MCP-capable agent (Claude Code, Codex, Cursor, Copilot)
run shell commands in a checkpointed Archil sandbox and rewind its own mistakes.

Run over stdio:  .venv/bin/python mcp_server.py
ENGINE=fake uses the in-memory FakeEngine (no network); the default is archil.
Step numbers in every tool are 1-based, matching the web UI.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer

sys.path.insert(0, str(Path(__file__).resolve().parent))
load_dotenv(Path(__file__).resolve().parent / ".env")
logging.getLogger("httpx").setLevel(logging.WARNING)

INSTRUCTIONS = """\
Undo gives you a remote Linux sandbox (Debian, python3, no pip) where every command is
checkpointed, so any step can be undone. The sandbox is NOT the user's machine; work in /workspace.
For pytest, run `apt-get update -qq`, then `apt-get install -y -qq python3-pytest`, then `python3 -m pytest`.
- Use `run` for every shell command meant for the sandbox. Run exactly ONE simple command per call:
  no &&, ;, || or | chains, and create each file with its own command, so each action can be undone.
- If a command breaks something (deleted the wrong files, corrupted a file, bad install), call
  `rewind` with the step number of the bad command to restore the state from just before it.
- Call `timeline` to see the steps so far and which files each one changed.
"""

mcp = MCPServer("undo", instructions=INSTRUCTIONS)
_lock = threading.Lock()
_engine: Any = None
_rewinds: list[dict] = []
_STATE = Path(tempfile.gettempdir()) / f"undo-mcp-{os.getpid()}.json"


@contextmanager
def _session() -> Iterator[None]:
    """Serialize tool calls and record live sandbox ids for the watchdog afterwards."""
    with _lock:
        try:
            yield
        finally:
            ids = _engine.sandbox_ids() if hasattr(_engine, "sandbox_ids") else []
            tmp = _STATE.with_suffix(".tmp")
            tmp.write_text(json.dumps(ids))
            tmp.replace(_STATE)


def _get_engine() -> Any:
    global _engine
    if _engine is None:
        if os.getenv("ENGINE", "archil").lower() == "fake":
            from fake_engine import FakeEngine

            engine = FakeEngine()
        else:
            from engine import ArchilEngine

            engine = ArchilEngine()
        engine.start()
        _engine = engine
    return _engine


def _hashes_before(engine: Any, index: int) -> Optional[dict[str, str]]:
    for step in reversed(engine.steps()[:index]):
        if step.file_hashes is not None:
            return step.file_hashes
    return getattr(engine, "initial_file_hashes", None)


def _step_dict(step: Any) -> dict:
    return {
        "step": step.index + 1,
        "command": step.command,
        "exit_code": step.exit_code,
        "stdout": step.stdout,
        "stderr": step.stderr,
        "files_added": step.added,
        "files_removed": step.removed,
        "files_modified": step.modified,
        "checkpoint_s": round(step.fork_s, 2),
    }


@mcp.tool()
def run(command: str) -> dict:
    """Checkpoint the sandbox, then run ONE shell command in /workspace.

    Returns the step number (use it with `rewind`), exit code, output, and the files
    the command added, removed or modified. Change tracking covers regular files only;
    __pycache__ and .pytest_cache are ignored. Rewind still restores everything.
    """
    with _session():
        return _step_dict(_get_engine().run(command))


@mcp.tool()
def rewind(step: int, reason: str = "") -> dict:
    """Undo step `step` and everything after it, restoring the sandbox to just before it ran.

    Undone steps leave the step list and later commands reuse their numbers; the rewind
    itself, with `reason` and the undone commands, is kept in `timeline()["rewinds"]`.
    """
    with _session():
        engine = _get_engine()
        steps = engine.steps()
        if not 1 <= step <= len(steps):
            return {"error": f"No step {step}. Valid steps are 1..{len(steps)}."}
        before, after = steps[-1].file_hashes, _hashes_before(engine, step - 1)
        restored = []
        if before is not None and after is not None:
            restored = sorted(p for p in before.keys() | after.keys() if before.get(p) != after.get(p))
        undone = [f"{s.index + 1}. {s.command}" for s in steps[step - 1:]]
        engine.rewind(step - 1)
        _rewinds.append({"rewound_to_before_step": step, "reason": reason,
                         "undone_steps": undone, "files_restored": restored})
        return {
            "rewound_to_before_step": step,
            "reason": reason,
            "files_restored": restored,
            "steps_remaining": len(engine.steps()),
            "next_step": len(engine.steps()) + 1,
        }


@mcp.tool()
def timeline() -> dict:
    """List the current steps (command, exit code, changed files) and every rewind so far.

    `rewinds` is in chronological order; each entry lists the steps it undid and why.
    """
    with _session():
        steps = _get_engine().steps()
        return {
            "steps": [
                {k: v for k, v in _step_dict(s).items() if k not in ("stdout", "stderr")}
                for s in steps
            ],
            "rewinds": _rewinds,
        }


@mcp.tool()
def reset() -> dict:
    """Delete the sandbox and all checkpoints and start over with an empty /workspace."""
    global _engine
    with _session():
        if _engine is not None:
            _engine.cleanup()
            _engine = None
        _rewinds.clear()
        _get_engine()
        return {"ok": True, "message": "Started a fresh sandbox."}


def _watchdog(server_pid: int, state: Path) -> None:
    """Outlive the server however it ends (closed, killed, crashed), then delete its sandboxes."""
    while True:
        try:
            os.kill(server_pid, 0)
        except ProcessLookupError:
            break
        except PermissionError:
            pass
        time.sleep(1)
    try:
        ids = json.loads(state.read_text())
    except (OSError, ValueError):
        return
    if ids:
        import archil
        from engine import delete_children_first

        sandboxes = []
        for sandbox_id in ids:
            try:
                sandboxes.append(archil.get_sandbox(sandbox_id))
            except Exception:
                pass
        delete_children_first(sandboxes)
    state.unlink(missing_ok=True)


if __name__ == "__main__":
    if sys.argv[1:2] == ["--watchdog"]:
        _watchdog(int(sys.argv[2]), Path(sys.argv[3]))
        sys.exit(0)
    if os.getenv("ENGINE", "archil").lower() != "fake":
        subprocess.Popen(
            [sys.executable, os.path.abspath(__file__), "--watchdog", str(os.getpid()), str(_STATE)],
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    mcp.run()
