"""Streamlit UI for running an agent with rewindable command history.

Run with ``streamlit run app.py``.  ``ENGINE=fake`` is intentionally the
default so this page can be explored without any cloud credentials.
"""
from __future__ import annotations

import atexit
import os
from typing import Any

import streamlit as st

import agent

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
        from engine import ArchilEngine  # Lazy: fake mode needs no Archil deps.

        return ArchilEngine()

    from fake_engine import FakeEngine

    return FakeEngine()


def _ensure_engine() -> Any:
    """Create and start exactly one engine for this browser session."""
    kind = _engine_kind()
    if "engine" not in st.session_state:
        try:
            engine = _new_engine(kind)
            with st.spinner("Starting sandbox…"):
                engine.start()
        except Exception as exc:
            st.error(_friendly_error("Starting the sandbox", exc))
            st.stop()
        atexit.register(engine.cleanup)
        st.session_state.engine = engine
        st.session_state.engine_kind = kind
        st.session_state.agent_unavailable = False
        st.session_state.rewound_step = None
        st.session_state.live_steps = []
    return st.session_state.engine


def _reset_engine() -> None:
    old_engine = st.session_state.pop("engine", None)
    if old_engine is not None:
        try:
            with st.spinner("Deleting old sandboxes…"):
                old_engine.cleanup()
        except Exception:
            pass
    st.session_state.pop("last_agent_result", None)
    _ensure_engine()


def _render_step(step: Any) -> None:
    """Render one immutable command checkpoint card."""
    with st.container(border=True):
        left, right = st.columns([4, 1])
        left.subheader(f"Step {step.index + 1}")
        with right:
            if step.exit_code == 0:
                st.markdown(f":green[**Exit code: {step.exit_code}**]")
            else:
                st.markdown(f":red[**Exit code: {step.exit_code}**]")

        st.code(step.command, language="bash")
        st.caption(f"Fork time: {step.fork_s:.3f}s")
        with st.expander("Output"):
            output = step.stdout
            if step.stderr:
                output += ("\n" if output else "") + step.stderr
            st.code(output or "(no output)", language="text")

        if st.button(
            "⏪ Rewind to before this step",
            key=f"rewind-{step.index}",
            use_container_width=True,
        ):
            try:
                with st.spinner(f"Rewinding to before step {step.index + 1}…"):
                    st.session_state.engine.rewind(step.index)
            except Exception as exc:
                st.session_state.flash_error = _friendly_error("Rewind", exc)
            else:
                st.session_state.rewound_step = step.index + 1
            st.rerun()


def _run_agent(engine: Any, instruction: str, live_area: Any) -> None:
    """Run an instruction and keep the timeline visibly live while it runs."""
    if not instruction.strip():
        st.warning("Enter an instruction first.")
        return

    st.session_state.live_steps = []

    def on_step(step: Any) -> None:
        # The engine already owns the canonical history.  This placeholder is
        # only for immediate feedback during a long-running agent invocation.
        st.session_state.live_steps.append(step)
        live_area.markdown(
            "\n\n".join(
                f"Running step {item.index + 1}: `{item.command}`"
                for item in st.session_state.live_steps
            )
        )

    try:
        with st.spinner("Agent is working…"):
            result = agent.run_agent(
                engine,
                instruction,
                on_step=on_step,
                max_steps=MAX_AGENT_STEPS,
            )
    except NotImplementedError:
        st.session_state.agent_unavailable = True
        st.info("The agent loop is not available yet. Run a command directly below.")
    except Exception as exc:
        st.error(_friendly_error("The agent run", exc) + " Steps that finished are kept below.")
        st.session_state.rewound_step = None
    else:
        if result:
            st.session_state.last_agent_result = result
        st.session_state.rewound_step = None
    finally:
        # The complete cards are rendered by this script run after the action.
        live_area.empty()


def main() -> None:
    st.set_page_config(page_title="Undo for Agents", page_icon="⏪", layout="wide")
    engine = _ensure_engine()

    st.title("⏪ Undo for Agents")
    st.caption("Agents make mistakes. Mine can time-travel.")

    flash_error = st.session_state.pop("flash_error", None)
    if flash_error:
        st.error(flash_error)

    with st.sidebar:
        st.subheader("Session")
        st.write(f"Engine type: `{st.session_state.engine_kind}`")
        steps = engine.steps()
        average = sum(step.fork_s for step in steps) / len(steps) if steps else 0.0
        st.write(f"Average fork time: `{average:.3f}s`")
        if st.button("Reset", use_container_width=True):
            _reset_engine()
            st.rerun()

    rewound_step = st.session_state.get("rewound_step")
    if rewound_step is not None:
        st.info(f"Rewound to before step {rewound_step}")
        continuation = st.text_input("Continue from here", key="continuation")
        if st.button("Continue", key="continue-agent"):
            _run_agent(engine, continuation, st.empty())

    task = st.text_area("Task", placeholder="Ask the agent what to do…", key="task")
    if st.button("Run", type="primary"):
        _run_agent(engine, task, st.empty())

    if st.session_state.get("agent_unavailable", False):
        st.subheader("Run command")
        command = st.text_input("Command", placeholder="ls", key="direct-command")
        if st.button("Run command", key="run-command"):
            if command.strip():
                try:
                    engine.run(command)
                except Exception as exc:
                    st.error(_friendly_error("Running the command", exc))
                st.session_state.rewound_step = None
            else:
                st.warning("Enter a command first.")

    if st.session_state.get("last_agent_result"):
        with st.expander("Agent response"):
            st.write(st.session_state.last_agent_result)

    st.subheader("Timeline")
    steps = engine.steps()
    if not steps:
        st.caption("No steps yet. Run a task or command to create the first checkpoint.")
    for step in steps:
        _render_step(step)


main()
