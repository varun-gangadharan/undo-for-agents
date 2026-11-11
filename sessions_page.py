"""Agent sessions page: watch MCP agents (Claude Code, Codex, ...) work live and rewind their steps.

Reads the shared Store written by mcp_server.py. Rewinds are queued as commands and
executed by that agent's MCP server process, which owns the sandbox.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import streamlit as st

import ui
from store import Store

ROOT = Path(__file__).resolve().parent


@st.cache_resource
def _store() -> Store:
    return Store()


def _session_label(session: dict) -> str:
    started = datetime.fromtimestamp(session["created_at"]).strftime("%b %d, %H:%M")
    status = "🟢" if session["live"] else "⚪"
    steps = session["step_count"]
    return f"{status} {ui.agent_label(session['agent'])} · {started} · {steps} step{'s' if steps != 1 else ''}"


def _render_setup() -> None:
    python = ROOT / ".venv" / "bin" / "python"
    command = f"{python if python.exists() else sys.executable} {ROOT / 'mcp_server.py'}"
    st.markdown("Add the Undo MCP server to your agent, then ask it to use the **undo** tools.")
    claude, codex, other = st.tabs(["Claude Code", "Codex", "Cursor / VS Code"])
    claude.code(f"claude mcp add -s user undo -- {command}", language="bash")
    codex.code(f"codex mcp add undo -- {command}", language="bash")
    codex.caption('Optional, in ~/.codex/config.toml under [mcp_servers.undo]: default_tools_approval_mode = "approve"')
    cmd, *args = command.split(" ")
    other.code(
        '{\n  "mcpServers": {\n    "undo": {\n'
        f'      "command": "{cmd}",\n      "args": ["{args[0]}"]\n'
        "    }\n  }\n}",
        language="json",
    )
    st.caption("Try: *Using the undo tools, set up a small Python project in /workspace with a passing test.*")


def _render_command_status(session_id: str, step_count: int) -> bool:
    """Show the state of a rewind queued from this page. Returns True while it is pending."""
    pending = st.session_state.get("ext_pending")
    if pending and pending["session"] == session_id:
        command = _store().get_command(pending["id"])
        if command is None or command["status"] == "pending":
            st.info(
                f"⏳ Rewinding to before step {pending['step']}… "
                "The agent's server applies it between commands."
            )
            return True
        st.session_state.ext_pending = None
        result = command["result"] or {}
        if command["status"] == "done":
            st.session_state.ext_banner = {
                "session": session_id, "step": pending["step"],
                "restored": result.get("files_restored", []), "removed": result.get("files_removed", []),
                "steps_at": result.get("steps_remaining"),
            }
        else:
            st.session_state.ext_error = result.get("error", "Rewind failed.")

    if error := st.session_state.pop("ext_error", None):
        st.error(f"Rewind failed: {error}")
    banner = st.session_state.get("ext_banner")
    if banner and banner["session"] == session_id and banner["steps_at"] == step_count:
        summary = ui.rewind_summary(banner["restored"], banner.get("removed", []))
        st.success(f"⏪ Rewound to before step {banner['step']}." + (f" {summary}" if summary else ""))
    return False


@st.fragment(run_every=2)
def _live_timeline(session_id: str, show_diff: bool) -> None:
    store = _store()
    known = st.session_state.get("ext_known_sessions")
    current = [(s["id"], s["live"]) for s in store.list_sessions()]
    if known is not None and current != known:
        st.rerun()  # a session started or ended: refresh the picker

    session = store.get_session(session_id)
    if session is None:
        st.warning("This session was deleted.")
        return
    steps = store.get_steps(session_id)
    agent = ui.agent_label(session["agent"])

    status = ":green-badge[● Live]" if session["live"] else ":gray-badge[Ended]"
    st.markdown(f"{status} **{agent}** · engine `{session['engine']}` · {len(steps)} steps")
    if not session["live"]:
        st.caption("The agent's session has ended and its sandbox was deleted, so rewinding is off.")

    pending = _render_command_status(session_id, len(steps))
    rewinds = ui.visible_rewinds(store.get_rewinds(session_id))

    def marker(rewind: dict) -> None:
        who = "⏪ **You" if rewind["source"] == "user" else f"🩹 **{agent}"
        ui.render_marker(f"{who} rewound to before step {rewind['before_step']}**", rewind["reason"], rewind["restored"], rewind.get("removed", []))

    if not steps and not rewinds:
        st.caption("No commands yet. The timeline fills in as the agent works.")
    for step in steps:
        for rewind in rewinds:
            if rewind["before_step"] == step.index + 1:
                marker(rewind)
        clicked = ui.render_step(
            step, show_diff, rewind_key=f"ext-rewind-{session_id}-{step.index}",
            disabled=pending or not session["live"],
        )
        if clicked:
            command_id = store.request_rewind(session_id, step.index + 1)
            st.session_state.ext_pending = {"id": command_id, "session": session_id, "step": step.index + 1}
            st.rerun()
    for rewind in rewinds:
        if rewind["before_step"] > len(steps):
            marker(rewind)


def render() -> None:
    st.title("🔌 Agent sessions")
    st.caption("Watch Claude Code, Codex or any MCP agent work in its sandbox, and rewind any step.")

    store = _store()
    sessions = store.list_sessions()
    st.session_state.ext_known_sessions = [(s["id"], s["live"]) for s in sessions]

    with st.sidebar:
        st.subheader("Agent sessions")
        show_diff = st.toggle("📂 Show what changed", value=True, key="ext_show_diff")
        if st.button("Clear ended sessions", use_container_width=True):
            st.toast(f"Removed {store.delete_ended_sessions()} ended sessions.")
            st.rerun()

    with st.expander("Connect an agent", expanded=not sessions):
        _render_setup()

    if not sessions:
        st.info("Waiting for an agent to connect…")
        _live_timeline_placeholder()
        return

    by_id = {s["id"]: s for s in sessions}
    default = next((s["id"] for s in sessions if s["live"]), sessions[0]["id"])
    if st.session_state.get("ext_session") not in by_id:
        st.session_state.ext_session = default
    session_id = st.selectbox(
        "Session", list(by_id), key="ext_session", format_func=lambda sid: _session_label(by_id[sid])
    )
    _live_timeline(session_id, show_diff)


@st.fragment(run_every=2)
def _live_timeline_placeholder() -> None:
    if _store().list_sessions(limit=1):
        st.rerun()
