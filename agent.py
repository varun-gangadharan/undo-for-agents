"""The LLM agent loop.

Gives Claude a `run_bash` tool (backed by engine.run) and a `web_search` tool
(keenable). Loops until Claude answers without a tool call or max_steps commands
have run, then returns Claude's final text. Works with any contracts.Engine.
"""
from __future__ import annotations

from typing import Callable, Optional

import anthropic
from dotenv import load_dotenv

from contracts import Engine, Step

load_dotenv()

MODEL = "claude-sonnet-5-5"
MAX_OUTPUT = 3000

SYSTEM_PROMPT = (
    "You are an agent working inside a Linux sandbox. The working directory is /workspace. "
    "Complete the user's task with the run_bash tool. Each run_bash call must be exactly ONE "
    "simple command: never chain commands with &&, ;, || or |, because every call becomes a "
    "separate rewindable checkpoint. Create each file with its own command. "
    "Check each command's output before moving on. "
    "The sandbox is Debian with python3 but no pip. If you need pytest, run "
    "`apt-get update -qq`, then `apt-get install -y -qq python3-pytest`, then use `python3 -m pytest`. "
    "Use web_search only if you need information you don't have. "
    "When the task is done, stop calling tools and reply with a one-sentence summary."
)

TOOLS = [
    {
        "name": "run_bash",
        "description": "Run a single shell command in the sandbox and return its exit code, stdout and stderr.",
        "input_schema": {
            "type": "object",
            "properties": {"command": {"type": "string", "description": "The shell command to run."}},
            "required": ["command"],
            "additionalProperties": False,
        },
    },
    {
        "name": "web_search",
        "description": "Search the web and return a short text summary of the top results.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "The search query."}},
            "required": ["query"],
            "additionalProperties": False,
        },
    },
]


def _truncate(text: str) -> str:
    if len(text) <= MAX_OUTPUT:
        return text
    return text[:MAX_OUTPUT] + f"\n...[truncated {len(text) - MAX_OUTPUT} chars]"


def _format_step(step: Step) -> str:
    return _truncate(
        f"exit_code: {step.exit_code}\nstdout:\n{step.stdout}\nstderr:\n{step.stderr}"
    )


def _web_search(query: str) -> str:
    try:
        from keenable import Keenable

        return _truncate(Keenable().search(query).to_context())
    except Exception as e:
        return f"web_search failed: {e}"


def _build_user_message(engine: Engine, task: str) -> str:
    history = engine.steps()
    if not history:
        return task
    lines = "\n".join(f"$ {s.command}  (exit {s.exit_code})" for s in history)
    return f"Commands already run in this sandbox:\n{lines}\n\nNew task: {task}"


def _final_text(response) -> str:
    return "".join(b.text for b in response.content if b.type == "text").strip()


def run_agent(
    engine: Engine,
    task: str,
    on_step: Optional[Callable[[Step], None]] = None,
    max_steps: int = 8,
) -> str:
    client = anthropic.Anthropic()
    messages: list[dict] = [{"role": "user", "content": _build_user_message(engine, task)}]
    steps_run = 0

    while True:
        response = client.messages.create(
            model=MODEL,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )
        if response.stop_reason == "refusal":
            return "The model declined this task."

        tool_uses = [b for b in response.content if b.type == "tool_use"]
        if not tool_uses:
            return _final_text(response)

        messages.append({"role": "assistant", "content": response.content})

        results = []
        for block in tool_uses:
            if block.name == "run_bash" and steps_run >= max_steps:
                output, is_error = f"Step limit of {max_steps} commands reached; not run.", True
            elif block.name == "run_bash":
                step = engine.run(block.input["command"])
                steps_run += 1
                if on_step:
                    on_step(step)
                output, is_error = _format_step(step), False
            elif block.name == "web_search":
                output, is_error = _web_search(block.input["query"]), False
            else:
                output, is_error = f"Unknown tool: {block.name}", True
            results.append(
                {"type": "tool_result", "tool_use_id": block.id, "content": output, "is_error": is_error}
            )
        messages.append({"role": "user", "content": results})

        if steps_run >= max_steps:
            return _final_text(response) or f"Stopped after {max_steps} commands."
