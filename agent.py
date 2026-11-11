"""The LLM agent loop.

Job: give Claude (anthropic SDK, ANTHROPIC_API_KEY) a `bash` tool. Each time Claude
calls it, run the command via engine.run(cmd), call on_step(step) so the UI can
update, and send the output back to Claude. Stop when Claude answers without a
tool call or after max_steps commands. Return Claude's final text answer.

Optionally use keenable (KEENABLE_API_KEY) for web search as a second tool.
Works with any contracts.Engine, so develop against FakeEngine.
"""
from __future__ import annotations

from typing import Callable, Optional

from contracts import Engine, Step


def run_agent(
    engine: Engine,
    task: str,
    on_step: Optional[Callable[[Step], None]] = None,
    max_steps: int = 8,
) -> str:
    raise NotImplementedError
