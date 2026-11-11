"""Run the agent once against FakeEngine and print each step as it happens."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import run_agent
from contracts import Step
from fake_engine import FakeEngine


def print_step(step: Step) -> None:
    print(f"[{step.index}] $ {step.command}  (exit {step.exit_code})")
    if step.stdout:
        print("    " + step.stdout.rstrip().replace("\n", "\n    "))
    if step.stderr:
        print("    stderr: " + step.stderr.rstrip())


def main() -> None:
    engine = FakeEngine()
    engine.start()
    try:
        result = run_agent(engine, "create a python file that prints hello and run it", on_step=print_step)
        print("\nFinal answer:", result)
    finally:
        engine.cleanup()


if __name__ == "__main__":
    main()
