"""MCP server: lets any MCP-capable agent (Claude Code, Codex, Cursor, Copilot)
run shell commands in a checkpointed Archil sandbox and rewind its own mistakes.

Run over stdio:  .venv/bin/python mcp_server.py
ENGINE=fake uses the in-memory FakeEngine (no network); the default is archil.
Step numbers in every tool are 1-based, matching the web UI.

Every session is mirrored to the shared Store, so the web UI's "Agent sessions" page can
watch it live and queue rewinds, which a background thread here executes.
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
from mcp.server.mcpserver import Context, MCPServer

sys.path.insert(0, str(Path(__file__).resolve().parent))
load_dotenv(Path(__file__).resolve().parent / ".env")
logging.getLogger("httpx").setLevel(logging.WARNING)

from store import Store  # noqa: E402
from engine import rewind_changes  # noqa: E402

INSTRUCTIONS = """\
Undo gives you a remote Linux sandbox (Debian, python3, no pip) where every command is
checkpointed, so any step can be undone. The sandbox is NOT the user's machine; work in /workspace.
For pytest, run `apt-get update -qq`, then `apt-get install -y -qq python3-pytest`, then `python3 -m pytest`.
- Use `run` for every shell command meant for the sandbox. Run exactly ONE simple command per call:
  no &&, ;, || or | chains, and create each file with its own command, so each action can be undone.
- If a command breaks something (deleted the wrong files, corrupted a file, bad install), call
  `rewind` with the step number of the bad command to restore the state from just before it.
- Call `timeline` to see the steps so far and which files each one changed.
- The user can also rewind from the Undo web UI. When a tool result has a `notice`, the sandbox
  was rewound by the user: re-check state before continuing.
"""

ENGINE_KIND = "fake" if os.getenv("ENGINE", "archil").lower() == "fake" else "archil"

mcp = MCPServer("undo", instructions=INSTRUCTIONS)
_lock = threading.Lock()
_engine: Any = None
_session_id: Optional[str] = None
_client_name = "agent"
_notices: list[str] = []
_store = Store()
_STATE = Path(tempfile.gettempdir()) / f"undo-mcp-{os.getpid()}.json"


def _write_state() -> None:
    """Tell the watchdog which sandboxes and session to clean up if this process dies."""
    ids = _engine.sandbox_ids() if hasattr(_engine, "sandbox_ids") else []
    tmp = _STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"session_id": _session_id, "sandbox_ids": ids}))
    tmp.replace(_STATE)


def _sync() -> None:
    _write_state()
    if _session_id is not None and _engine is not None:
        _store.save_steps(_session_id, _engine.steps())


@contextmanager
def _session(ctx: Optional[Context] = None) -> Iterator[None]:
    """Serialize engine access, then mirror the result to the store and watchdog."""
    global _client_name
    if ctx is not None:
        params = ctx.session.client_params
        if params is not None and params.client_info is not None:
            _client_name = params.client_info.name
    with _lock:
        try:
            yield
        finally:
            _sync()


def _get_engine() -> Any:
    global _engine, _session_id
    if _engine is None:
        if ENGINE_KIND == "fake":
            from fake_engine import FakeEngine

            engine = FakeEngine()
        else:
            from engine import ArchilEngine

            engine = ArchilEngine()
        engine.start()
        _engine = engine
        _session_id = _store.create_session(agent=_client_name, engine=ENGINE_KIND)
        _sync()
    return _engine


def _with_notices(result: dict) -> dict:
    if _notices:
        result["notice"] = " ".join(_notices)
        _notices.clear()
    return result


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


def _do_rewind(step: int, reason: str, source: str) -> dict:
    """Rewind to before 1-based `step`. Caller holds _lock."""
    engine = _get_engine()
    steps = engine.steps()
    if not 1 <= step <= len(steps):
        return {"error": f"No step {step}. Valid steps are 1..{len(steps)}."}
    restored, removed = rewind_changes(steps[-1].file_hashes, _hashes_before(engine, step - 1))
    undone = [f"{s.index + 1}. {s.command}" for s in steps[step - 1:]]
    engine.rewind(step - 1)
    _store.add_rewind(_session_id, step, reason, source, undone, restored, removed)
    return {
        "rewound_to_before_step": step,
        "reason": reason,
        "undone_steps": undone,
        "files_restored": restored,
        "files_removed": removed,
        "steps_remaining": len(engine.steps()),
        "next_step": len(engine.steps()) + 1,
    }


@mcp.tool()
def run(command: str, ctx: Context) -> dict:
    """Checkpoint the sandbox, then run ONE shell command in /workspace.

    Returns the step number (use it with `rewind`), exit code, output, and the files
    the command added, removed or modified. Change tracking covers regular files only;
    __pycache__ and .pytest_cache are ignored. Rewind still restores everything.
    """
    with _session(ctx):
        return _with_notices(_step_dict(_get_engine().run(command)))


@mcp.tool()
def rewind(step: int, ctx: Context, reason: str = "") -> dict:
    """Undo step `step` and everything after it, restoring the sandbox to just before it ran.

    Undone steps leave the step list and later commands reuse their numbers; the rewind
    itself, with `reason` and the undone commands, is kept in `timeline()["rewinds"]`.
    """
    with _session(ctx):
        return _with_notices(_do_rewind(step, reason, source="agent"))


@mcp.tool()
def timeline(ctx: Context) -> dict:
    """List the current steps (command, exit code, changed files) and every rewind so far.

    `rewinds` is in chronological order; each entry says who rewound (agent or user), why,
    and which steps it undid.
    """
    with _session(ctx):
        steps = _get_engine().steps()
        return _with_notices({
            "steps": [
                {k: v for k, v in _step_dict(s).items() if k not in ("stdout", "stderr")}
                for s in steps
            ],
            "rewinds": [
                {k: r[k] for k in ("before_step", "source", "reason", "undone", "restored", "removed")}
                for r in _store.get_rewinds(_session_id)
            ],
        })


@mcp.tool()
def reset(ctx: Context) -> dict:
    """Delete the sandbox and all checkpoints and start over with an empty /workspace."""
    global _engine, _session_id
    with _session(ctx):
        if _engine is not None:
            _engine.cleanup()
            _store.end_session(_session_id)
            _engine, _session_id = None, None
        _notices.clear()
        _get_engine()
        return {"ok": True, "message": "Started a fresh sandbox."}


def _serve_ui_commands() -> None:
    """Heartbeat the session and execute rewinds queued from the web UI."""
    while True:
        time.sleep(1)
        session_id = _session_id
        if session_id is None:
            continue
        try:
            _store.heartbeat(session_id)
            for command in _store.pending_commands(session_id):
                with _lock:
                    if session_id != _session_id:
                        break
                    try:
                        result = _do_rewind(command["step"], command["reason"], source="user")
                    except Exception as exc:
                        result = {"error": f"{type(exc).__name__}: {exc}"}
                    finally:
                        _sync()
                    _store.finish_command(command["id"], "error" not in result, result)
                    if "error" not in result:
                        _notices.append(
                            f"The user rewound the sandbox to before step {command['step']} from the web UI"
                            + (f" ({command['reason']})" if command["reason"] else "")
                            + f". Undone: {'; '.join(result['undone_steps'])}."
                            + f" The next command will be step {result['next_step']}."
                        )
        except Exception:
            pass


def _watchdog(server_pid: int, state: Path) -> None:
    """Outlive the server however it ends (closed, killed, crashed), then clean up after it."""
    while True:
        try:
            os.kill(server_pid, 0)
        except ProcessLookupError:
            break
        except PermissionError:
            pass
        time.sleep(1)
    try:
        data = json.loads(state.read_text())
    except (OSError, ValueError):
        return
    if data.get("session_id"):
        try:
            Store().end_session(data["session_id"])
        except Exception:
            pass
    if data.get("sandbox_ids"):
        import archil
        from engine import delete_children_first

        sandboxes = []
        for sandbox_id in data["sandbox_ids"]:
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
    subprocess.Popen(
        [sys.executable, os.path.abspath(__file__), "--watchdog", str(os.getpid()), str(_STATE)],
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    threading.Thread(target=_serve_ui_commands, daemon=True).start()
    mcp.run()
