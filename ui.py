"""Streamlit rendering shared by the Playground and Agent sessions pages."""
from __future__ import annotations

from typing import Any, Optional

import streamlit as st

from engine import rewind_changes  # noqa: F401  (re-exported for the pages)

_AGENT_NAMES = {
    "claude-code": "Claude Code",
    "codex-mcp-client": "Codex",
    "codex": "Codex",
    "cursor-vscode": "Cursor",
    "cursor": "Cursor",
    "visual studio code": "VS Code",
    "mcp": "MCP client",
}


def agent_label(client_name: str) -> str:
    return _AGENT_NAMES.get(client_name.lower(), client_name)


def md(text: str) -> str:
    for ch in "\\`*_[]<>#|~$":
        text = text.replace(ch, "\\" + ch)
    return text


def render_chips(step: Any) -> None:
    chips = (
        [f":green-badge[+ {md(p)}]" for p in getattr(step, "added", [])]
        + [f":red-badge[− {md(p)}]" for p in getattr(step, "removed", [])]
        + [f":yellow-badge[\\~ {md(p)}]" for p in getattr(step, "modified", [])]
    )
    if chips:
        st.markdown(" ".join(chips))


def render_marker(title: str, reason: str, restored: list[str], removed: list[str]) -> None:
    with st.container(border=True):
        st.markdown(f"{title}" + (f": {md(reason)}" if reason else ""))
        st.caption(rewind_summary(restored, removed) or "No tracked files changed.")


def rewind_summary(restored: list[str], removed: list[str]) -> str:
    parts = []
    if restored:
        parts.append("Restored: " + ", ".join(restored))
    if removed:
        parts.append("Removed: " + ", ".join(removed))
    return " · ".join(parts)


def render_step(step: Any, show_diff: bool, rewind_key: Optional[str] = None, disabled: bool = False) -> bool:
    """Draw one step card. Returns True when its rewind button was clicked."""
    with st.container(border=True):
        left, right = st.columns([4, 1])
        left.subheader(f"Step {step.index + 1}")
        color = "green" if step.exit_code == 0 else "red"
        right.markdown(f":{color}[**Exit code: {step.exit_code}**]")

        st.code(step.command, language="bash")
        if show_diff:
            render_chips(step)
        st.caption(f"Fork time: {step.fork_s:.3f}s")
        with st.expander("Output"):
            output = step.stdout
            if step.stderr:
                output += ("\n" if output else "") + step.stderr
            st.code(output or "(no output)", language="text")

        if rewind_key is None:
            return False
        return st.button(
            "⏪ Rewind to before this step",
            key=rewind_key,
            use_container_width=True,
            disabled=disabled,
        )


def visible_rewinds(rewinds: list[dict]) -> list[dict]:
    """Rewind markers still on the current branch: a later rewind to an earlier step hides them."""
    visible: list[dict] = []
    for rewind in rewinds:
        visible = [r for r in visible if r["before_step"] <= rewind["before_step"]]
        visible.append(rewind)
    return visible
