"""Terminal-only demo: run the agent on a real ArchilEngine, then rewind or continue."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import run_agent
from contracts import Step
from engine import ArchilEngine

PREVIEW_CHARS = 200
MENU = "[r]ewind to step N / [c]ontinue with new instruction / [q]uit"


def _short(text: str) -> str:
    text = text.strip()
    return text if len(text) <= PREVIEW_CHARS else text[:PREVIEW_CHARS] + "…"


def print_step(step: Step) -> None:
    print(f"  step {step.index}: $ {step.command}  (exit {step.exit_code})")
    output = _short(step.stdout) or _short(step.stderr)
    if output:
        print("      " + output.replace("\n", "\n      "))


def print_history(engine: ArchilEngine) -> None:
    steps = engine.steps()
    if not steps:
        print("  (no steps yet)")
    for step in steps:
        print(f"  step {step.index}: $ {step.command}  (exit {step.exit_code})")


def run(engine: ArchilEngine, instruction: str) -> None:
    print(f"\n> {instruction}")
    try:
        result = run_agent(engine, instruction, on_step=print_step)
    except Exception as e:
        print(f"\nAgent failed: {type(e).__name__}: {e}\n")
        return
    print(f"\nAgent: {result}\n")


def rewind(engine: ArchilEngine, arg: str) -> None:
    raw = arg or input("Rewind to step N: ").strip()
    try:
        index = int(raw)
        engine.rewind(index)
    except ValueError:
        print(f"Not a step number: {raw!r}")
        return
    except IndexError as e:
        print(f"Can't rewind: {e}")
        return
    print(f"Rewound to before step {index}. Remaining history:")
    print_history(engine)


def main() -> None:
    engine = ArchilEngine()
    print("Starting sandbox…")
    engine.start()
    try:
        task = " ".join(sys.argv[1:]) or input("Task: ").strip()
        if task:
            run(engine, task)
        while True:
            choice = input(f"{MENU}\n> ").strip()
            cmd, _, arg = choice.partition(" ")
            cmd = cmd.lower()
            if cmd in ("q", "quit"):
                break
            if cmd in ("r", "rewind"):
                rewind(engine, arg.strip())
            elif cmd in ("c", "continue"):
                instruction = arg.strip() or input("New instruction: ").strip()
                if instruction:
                    run(engine, instruction)
            else:
                print("Please enter r, c, or q.")
    except (KeyboardInterrupt, EOFError):
        print()
    finally:
        print("Cleaning up sandbox…")
        engine.cleanup()


if __name__ == "__main__":
    main()
