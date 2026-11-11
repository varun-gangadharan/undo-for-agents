"""Streamlit UI. Run with: streamlit run app.py

Job:
- Keep one Engine in st.session_state (FakeEngine by default, ArchilEngine via a
  sidebar toggle), calling start() once.
- Text box for a task; on submit call agent.run_agent(engine, task, on_step=...)
  and render each Step live (command, stdout/stderr, exit code, fork_s).
- Show a timeline of engine.steps() with a "Rewind to here" button on each step
  that calls engine.rewind(step.index), after which the user can give a new task.
"""
import streamlit as st

st.title("Undo for Agents")
st.info("UI not built yet.")
