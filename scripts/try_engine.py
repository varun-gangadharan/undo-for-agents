"""Exercise ArchilEngine's checkpoint and rewind behavior."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine import ArchilEngine


def main() -> None:
    engine = ArchilEngine(name="undo-for-agents-try")
    try:
        engine.start()
        engine.run("echo hi > a.txt")
        engine.run("ls")
        engine.run("rm -rf /workspace/*")
        engine.rewind(2)

        restored = engine.run("ls")
        if "a.txt" not in restored.stdout.splitlines():
            raise AssertionError(f"rewind did not restore a.txt: {restored.stdout!r}")

        timings = [step.fork_s for step in engine.steps()]
        print(f"restored ls: {restored.stdout.strip()}")
        print("fork timings (s): " + ", ".join(f"{timing:.3f}" for timing in timings))
        print(f"average fork time (s): {sum(timings) / len(timings):.3f}")
    finally:
        engine.cleanup()


if __name__ == "__main__":
    main()
