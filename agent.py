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
SELF_UNDO_PROMPT = " Every command is checkpointed. If you break something, prefer rewind_to over manual fixes."
MAX_SELF_REWINDS = 2

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

REWIND_TOOL = {
    "name": "rewind_to",
    "description": (
        "Undo your own mistake: restore the sandbox to the state from BEFORE step_index ran. "
        "Use this when a check (like tests) fails because of something you changed earlier, "
        "instead of patching on top of the mistake."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "step_index": {"type": "integer", "description": "The step to undo; it and every later step are removed."},
            "reason": {"type": "string", "description": "Why this step was a mistake."},
        },
        "required": ["step_index", "reason"],
        "additionalProperties": False,
    },
}


def _truncate(text: str) -> str:
    if len(text) <= MAX_OUTPUT:
        return text
    return text[:MAX_OUTPUT] + f"\n...[truncated {len(text) - MAX_OUTPUT} chars]"


def _format_step(step: Step) -> str:
    return _truncate(
        f"This was step {step.index}.\n"
        f"exit_code: {step.exit_code}\nstdout:\n{step.stdout}\nstderr:\n{step.stderr}"
    )


def _list_steps(steps: list[Step]) -> str:
    if not steps:
        return "(none)"
    return "; ".join(f"step {s.index}: $ {s.command} (exit {s.exit_code})" for s in steps)


def _self_rewind(
    engine: Engine,
    tool_input: dict,
    rewinds_used: int,
    on_rewind: Optional[Callable[[int, str], None]],
) -> tuple[str, bool]:
    if rewinds_used >= MAX_SELF_REWINDS:
        return f"Rewind limit of {MAX_SELF_REWINDS} per run reached; fix forward instead.", True
    index, reason = tool_input.get("step_index"), tool_input.get("reason", "")
    existing = [s.index for s in engine.steps()]
    if not isinstance(index, int) or index not in existing:
        return f"No step {index}. Existing steps: {existing or 'none'}.", True
    engine.rewind(index)
    if on_rewind:
        on_rewind(index, reason)
    return (
        f"Sandbox restored to before step {index}. Steps {index} and later no longer exist. "
        f"Current steps: {_list_steps(engine.steps())}.",
        False,
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
    allow_self_undo: bool = False,
    on_rewind: Optional[Callable[[int, str], None]] = None,
) -> str:
    client = anthropic.Anthropic()
    system = SYSTEM_PROMPT + SELF_UNDO_PROMPT if allow_self_undo else SYSTEM_PROMPT
    tools = TOOLS + [REWIND_TOOL] if allow_self_undo else TOOLS
    messages: list[dict] = [{"role": "user", "content": _build_user_message(engine, task)}]
    steps_run = 0
    rewinds_used = 0

    while True:
        response = client.messages.create(
            model=MODEL,
            max_tokens=16000,
            system=system,
            tools=tools,
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
            elif block.name == "rewind_to" and allow_self_undo:
                output, is_error = _self_rewind(engine, block.input, rewinds_used, on_rewind)
                if not is_error:
                    rewinds_used += 1
            else:
                output, is_error = f"Unknown tool: {block.name}", True
            results.append(
                {"type": "tool_result", "tool_use_id": block.id, "content": output, "is_error": is_error}
            )
        messages.append({"role": "user", "content": results})

        if steps_run >= max_steps:
            return _final_text(response) or f"Stopped after {max_steps} commands."
