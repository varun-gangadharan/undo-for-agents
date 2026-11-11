"""Try the agent.

  python scripts/try_agent.py              live Claude + FakeEngine, simple task
  python scripts/try_agent.py --self-undo  offline check of rewind_to (FakeEngine, scripted Claude, no API calls)
  python scripts/try_agent.py --live-undo  live Claude + ArchilEngine self-undo demo
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agent
from agent import run_agent
from contracts import Step
from fake_engine import FakeEngine

LIVE_UNDO_TASK = (
    "In main.py only, rename the function add to add_numbers. Don't touch the tests. "
    "Then run python3 -m pytest /workspace. If the tests fail, undo your own mistake and run the tests again."
)
SEED_COMMANDS = [
    "printf 'def add(a, b):\\n    return a + b\\n' > main.py",
    "printf 'from main import add\\n\\n\\ndef test_add():\\n    assert add(2, 3) == 5\\n' > test_main.py",
    "apt-get update -qq && apt-get install -y -qq python3-pytest",
]


def print_step(step: Step) -> None:
    print(f"[{step.index}] $ {step.command}  (exit {step.exit_code})")
    output = (step.stdout or step.stderr).strip()
    if output:
        print("    " + output[-400:].replace("\n", "\n    "))


def print_rewind(index: int, reason: str) -> None:
    print(f"<< rewound to before step {index}: {reason}")


def simple() -> None:
    engine = FakeEngine()
    engine.start()
    try:
        result = run_agent(engine, "create a python file that prints hello and run it", on_step=print_step)
        print("\nFinal answer:", result)
    finally:
        engine.cleanup()


def _tool(name: str, **kw) -> NS:
    _tool.n = getattr(_tool, "n", 0) + 1
    return NS(type="tool_use", id=f"t{_tool.n}", name=name, input=kw)


def _scripted_client(turns: list[list[NS]], calls: list[dict]) -> NS:
    def create(**kw):
        calls.append(kw)
        content = turns[len(calls) - 1]
        return NS(content=content, stop_reason="tool_use" if content[0].type == "tool_use" else "end_turn")

    return NS(messages=NS(create=create))


def _last_results(calls: list[dict]) -> list[dict]:
    return calls[-1]["messages"][-1]["content"]


def self_undo_check() -> None:
    def check(label: str, ok: bool) -> None:
        print(f"{'PASS' if ok else 'FAIL'}  {label}")
        failures.extend([] if ok else [label])

    failures: list[str] = []

    calls: list[dict] = []
    turns = [[NS(type="text", text="done")]]
    with mock.patch.object(agent.anthropic, "Anthropic", return_value=_scripted_client(turns, calls)):
        engine = FakeEngine()
        engine.start()
        run_agent(engine, "noop")
    check("default run has no rewind_to tool", all(t["name"] != "rewind_to" for t in calls[0]["tools"]))
    check("default run has no self-undo prompt", agent.SELF_UNDO_PROMPT not in calls[0]["system"])

    calls = []
    rewinds: list[tuple[int, str]] = []
    turns = [
        [_tool("run_bash", command="echo broken > main.py")],
        [_tool("run_bash", command="cat main.py")],
        [_tool("rewind_to", step_index=7, reason="bad index")],
        [_tool("rewind_to", step_index=0, reason="I broke main.py")],
        [_tool("run_bash", command="cat main.py")],
        [_tool("rewind_to", step_index=0, reason="second")],
        [_tool("rewind_to", step_index=0, reason="third")],
        [NS(type="text", text="Restored main.py.")],
    ]
    with mock.patch.object(agent.anthropic, "Anthropic", return_value=_scripted_client(turns, calls)):
        engine = FakeEngine()
        engine.start()
        result = run_agent(
            engine, "break and fix", on_step=print_step, allow_self_undo=True,
            on_rewind=lambda i, r: (rewinds.append((i, r)), print_rewind(i, r)),
        )

    results = [m["content"][0] for m in calls[-1]["messages"] if m["role"] == "user" and isinstance(m["content"], list)]
    check("self-undo run exposes rewind_to", any(t["name"] == "rewind_to" for t in calls[0]["tools"]))
    check("self-undo prompt added", calls[0]["system"].endswith(agent.SELF_UNDO_PROMPT))
    check("run_bash result names its step", results[0]["content"].startswith("This was step 0."))
    check("invalid index is an error", results[2]["is_error"] and "No step 7" in results[2]["content"])
    check(
        "rewind result text",
        results[3]["content"].startswith("Sandbox restored to before step 0. Steps 0 and later no longer exist.")
        and not results[3]["is_error"],
    )
    check("file restored after rewind", results[4]["content"].endswith("print('hi')\n\nstderr:\n"))
    check("second rewind allowed", not results[5]["is_error"])
    check("third rewind refused", results[6]["is_error"] and "limit" in results[6]["content"])
    check("on_rewind called twice", rewinds == [(0, "I broke main.py"), (0, "second")])
    check("final text returned", result == "Restored main.py.")
    print("\nALL PASS" if not failures else f"\n{len(failures)} FAILED")


def live_undo() -> None:
    from engine import ArchilEngine

    engine = ArchilEngine()
    print("Starting sandbox and seeding project…")
    engine.start()
    try:
        for cmd in SEED_COMMANDS:
            print_step(engine.run(cmd))
        print("\nAgent run:")
        result = run_agent(engine, LIVE_UNDO_TASK, on_step=print_step, allow_self_undo=True, on_rewind=print_rewind)
        print("\nFinal answer:", result)
        print("\nIndependent check:")
        print_step(engine.run("cat main.py"))
        print_step(engine.run("python3 -m pytest -q /workspace"))
    finally:
        engine.cleanup()


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    {"--self-undo": self_undo_check, "--live-undo": live_undo}.get(mode, simple)()
