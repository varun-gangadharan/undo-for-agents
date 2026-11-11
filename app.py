"""Streamlit UI for running an agent with rewindable command history.

Run with ``streamlit run app.py``.  ``ENGINE=fake`` is intentionally the
default so this page can be explored without any cloud credentials.
"""
from __future__ import annotations

import atexit
import os
from typing import Any, Optional

import streamlit as st

import agent
import sessions_page
import ui

MAX_AGENT_STEPS = 8


def _friendly_error(action: str, exc: Exception) -> str:
    name = type(exc).__name__
    module = type(exc).__module__ or ""
    if module.startswith("anthropic"):
        if "Authentication" in name:
            return f"{action} failed: the Anthropic API key is missing or invalid. Check ANTHROPIC_API_KEY in .env."
        if "RateLimit" in name or "Overloaded" in name:
            return f"{action} failed: Claude is rate-limited or overloaded right now. Try again in a moment."
        body = getattr(exc, "body", None)
        detail = body.get("error", {}).get("message") if isinstance(body, dict) else None
        return f"{action} failed: Claude API error: {detail or name}."
    if module.startswith("archil"):
        return f"{action} failed: the Archil sandbox returned an error ({name}: {str(exc)[:200]})."
    if isinstance(exc, IndexError):
        return f"{action} failed: that step no longer exists. The timeline has been refreshed."
    return f"{action} failed: {name}: {str(exc)[:200]}"


def _engine_kind() -> str:
    """Return the configured engine, falling back to the safe demo engine."""
    kind = os.getenv("ENGINE", "fake").lower()
    return kind if kind in {"fake", "archil"} else "fake"


def _new_engine(kind: str) -> Any:
    """Build an engine without importing the optional Archil implementation."""
    if kind == "archil":
        from engine import ArchilEngine

        return ArchilEngine()

    from fake_engine import FakeEngine

    return FakeEngine()


def _ensure_engine() -> Any:
    """Create and start exactly one engine for this browser session."""
    kind = _engine_kind()
    if "engine" not in st.session_state:
        try:
            engine = _new_engine(kind)
            engine.track_files = st.session_state.get("show_diff", True)
            with st.spinner("Starting sandbox…"):
                engine.start()
        except Exception as exc:
            st.error(_friendly_error("Starting the sandbox", exc))
            st.stop()
        atexit.register(engine.cleanup)
        st.session_state.engine = engine
        st.session_state.engine_kind = kind
        st.session_state.agent_unavailable = False
        st.session_state.markers = []
        st.session_state.has_run = False
    return st.session_state.engine


def _reset_engine() -> None:
    old_engine = st.session_state.pop("engine", None)
    if old_engine is not None:
        try:
            with st.spinner("Deleting old sandboxes…"):
                old_engine.cleanup()
        except Exception:
            pass
    for key in ("last_agent_result", "restore_banner"):
        st.session_state.pop(key, None)
    _ensure_engine()


def _purge_all() -> None:
    from engine import purge_project_sandboxes

    st.session_state.pop("engine", None)  # its sandboxes are about to be deleted too
    for key in ("last_agent_result", "restore_banner"):
        st.session_state.pop(key, None)
    try:
        with st.spinner("Deleting all project sandboxes…"):
            deleted, leftover = purge_project_sandboxes()
    except Exception as exc:
        st.session_state.flash_error = _friendly_error("Deleting sandboxes", exc)
    else:
        msg = f"🧹 Deleted {deleted} sandbox{'es' if deleted != 1 else ''}."
        if leftover:
            st.session_state.flash_error = msg + f" {len(leftover)} could not be deleted: {', '.join(leftover)}"
        else:
            st.session_state.flash_success = msg
    st.rerun()


def _latest_hashes(engine: Any) -> Optional[dict[str, str]]:
    return _hashes_before(engine, len(engine.steps()))


def _hashes_before(engine: Any, index: int) -> Optional[dict[str, str]]:
    """File hashes as they were just before step `index` ran."""
    for step in reversed(engine.steps()[:index]):
        if step.file_hashes is not None:
            return step.file_hashes
    return getattr(engine, "initial_file_hashes", None)


def _render_timeline(engine: Any, show_diff: bool, interactive: bool) -> None:
    steps = engine.steps()
    markers = st.session_state.get("markers", [])
    if not steps and not markers:
        st.caption("No steps yet. Run a task to create the first checkpoint.")
    for step in steps:
        for marker in markers:
            if marker["at"] == step.index:
                _render_marker(marker)
        if ui.render_step(step, show_diff, rewind_key=f"rewind-{step.index}" if interactive else None):
            _rewind(step.index)
    for marker in markers:
        if marker["at"] >= len(steps):
            _render_marker(marker)


def _render_marker(marker: dict) -> None:
    ui.render_marker(
        f"🩹 **Agent rewound to before step {marker['at'] + 1}**",
        marker["reason"], marker.get("restored", []), marker.get("removed", []),
    )


def _rewind(index: int) -> None:
    engine = st.session_state.engine
    restored, removed = ui.rewind_changes(_latest_hashes(engine), _hashes_before(engine, index))
    try:
        with st.spinner(f"Rewinding to before step {index + 1}…"):
            engine.rewind(index)
    except Exception as exc:
        st.session_state.flash_error = _friendly_error("Rewind", exc)
    else:
        st.session_state.markers = [m for m in st.session_state.markers if m["at"] <= index]
        st.session_state.restore_banner = {"step": index + 1, "restored": restored, "removed": removed}
    st.rerun()


def _run_agent(engine: Any, instruction: str, timeline: Any, show_diff: bool, self_undo: bool) -> None:
    """Run an instruction, redrawing the timeline placeholder live as steps arrive."""
    if not instruction.strip():
        st.warning("Enter an instruction first.")
        return

    st.session_state.pop("restore_banner", None)
    last_hashes = [_latest_hashes(engine)]

    def redraw() -> None:
        with timeline.container():
            _render_timeline(engine, show_diff, interactive=False)

    def on_step(step: Any) -> None:
        if step.file_hashes is not None:
            last_hashes[0] = step.file_hashes
        redraw()

    def on_rewind(index: int, reason: str) -> None:
        restored, removed = ui.rewind_changes(last_hashes[0], _hashes_before(engine, index))
        last_hashes[0] = _hashes_before(engine, index)
        markers = [m for m in st.session_state.markers if m["at"] <= index]
        markers.append({"at": index, "reason": reason, "restored": restored, "removed": removed})
        st.session_state.markers = markers
        redraw()

    try:
        with st.spinner("Agent is working…"):
            try:
                result = agent.run_agent(
                    engine,
                    instruction,
                    on_step=on_step,
                    max_steps=MAX_AGENT_STEPS,
                    allow_self_undo=self_undo,
                    on_rewind=on_rewind,
                )
            except TypeError as exc:
                if "unexpected keyword argument" not in str(exc):
                    raise
                if self_undo:
                    st.session_state.flash_info = "Self-undo isn't available in this agent version yet."
                result = agent.run_agent(engine, instruction, on_step=on_step, max_steps=MAX_AGENT_STEPS)
    except NotImplementedError:
        st.session_state.agent_unavailable = True
    except Exception as exc:
        st.session_state.flash_error = (
            _friendly_error("The agent run", exc) + " Steps that finished are kept below."
        )
    else:
        if result:
            st.session_state.last_agent_result = result
        st.session_state.clear_task = True
    st.session_state.has_run = True
    st.rerun()


def main() -> None:
    engine = _ensure_engine()

    with st.sidebar:
        st.subheader("Session")
        st.write(f"Engine type: `{st.session_state.engine_kind}`")
        steps = engine.steps()
        average = sum(step.fork_s for step in steps) / len(steps) if steps else 0.0
        st.write(f"Average fork time: `{average:.3f}s`")
        show_diff = st.toggle("📂 Show what changed", value=True, key="show_diff")
        self_undo = st.toggle("🩹 Let the agent undo its own mistakes", value=False, key="self_undo")
        if st.button("Reset", use_container_width=True):
            _reset_engine()
            st.rerun()
        if st.session_state.get("confirm_purge"):
            st.warning("Deletes every `undo-for-agents-` sandbox on this Archil account, including other sessions'.")
            yes, no = st.columns(2)
            if yes.button("Delete all", type="primary", use_container_width=True):
                st.session_state.confirm_purge = False
                _purge_all()
            if no.button("Cancel", use_container_width=True):
                st.session_state.confirm_purge = False
                st.rerun()
        elif st.button("🧹 Delete all project sandboxes", use_container_width=True):
            st.session_state.confirm_purge = True
            st.rerun()
    engine.track_files = show_diff

    st.title("⏪ Undo for Agents")
    st.caption("Agents make mistakes. Mine can time-travel.")

    if flash_error := st.session_state.pop("flash_error", None):
        st.error(flash_error)
    if flash_info := st.session_state.pop("flash_info", None):
        st.info(flash_info)
    if flash_success := st.session_state.pop("flash_success", None):
        st.success(flash_success)

    banner = st.session_state.get("restore_banner")
    if banner:
        summary = ui.rewind_summary(banner["restored"], banner.get("removed", [])) if show_diff else ""
        if summary:
            st.success(f"⏪ {summary}")
            st.caption(f"Rewound to before step {banner['step']}.")
        else:
            st.info(f"⏪ Rewound to before step {banner['step']}.")

    if st.session_state.get("last_agent_result"):
        with st.expander("Agent response"):
            st.write(st.session_state.last_agent_result)

    st.subheader("Timeline")
    timeline = st.empty()
    with timeline.container():
        _render_timeline(engine, show_diff, interactive=True)

    if st.session_state.pop("clear_task", False):
        st.session_state.task = ""
    has_run = st.session_state.get("has_run") or bool(engine.steps())
    label = "Next instruction" if has_run else "Task"
    task = st.text_area(label, placeholder="Ask the agent what to do…", key="task")
    if st.button("Run", type="primary"):
        _run_agent(engine, task, timeline, show_diff, self_undo)

    if st.session_state.get("agent_unavailable", False):
        st.subheader("Run command")
        command = st.text_input("Command", placeholder="ls", key="direct-command")
        if st.button("Run command", key="run-command"):
            if command.strip():
                try:
                    engine.run(command)
                except Exception as exc:
                    st.session_state.flash_error = _friendly_error("Running the command", exc)
                st.rerun()
            else:
                st.warning("Enter a command first.")


st.set_page_config(page_title="Undo for Agents", page_icon="⏪", layout="wide")
st.navigation([
    st.Page(main, title="Playground", icon="⏪", url_path="playground", default=True),
    st.Page(sessions_page.render, title="Agent sessions", icon="🔌", url_path="sessions"),
]).run()
