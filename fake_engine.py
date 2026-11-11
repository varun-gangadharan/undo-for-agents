"""In-memory Engine for building the agent and UI without network access.

Understands a tiny subset of bash: ls, touch, rm, mkdir, cat, `echo X > file`.
Anything else returns canned output.
"""
from __future__ import annotations

import copy
import random
import shlex
import time

from contracts import Step


class FakeEngine:
    def __init__(self) -> None:
        self._files: dict[str, str] = {}
        self._steps: list[Step] = []
        self._checkpoints: list[dict[str, str]] = []
        self._started = False

    def start(self) -> None:
        self._files = {"README.md": "# demo project\n", "main.py": "print('hi')\n"}
        self._steps = []
        self._checkpoints = []
        self._started = True

    def run(self, cmd: str) -> Step:
        if not self._started:
            raise RuntimeError("call start() first")

        t0 = time.perf_counter()
        self._checkpoints.append(copy.deepcopy(self._files))
        fork_s = time.perf_counter() - t0 + random.uniform(0.2, 0.6)

        t1 = time.perf_counter()
        stdout, stderr, code = self._execute(cmd)
        duration_s = time.perf_counter() - t1 + random.uniform(0.05, 0.3)

        step = Step(len(self._steps), cmd, stdout, stderr, code, duration_s, fork_s)
        self._steps.append(step)
        return step

    def rewind(self, index: int) -> None:
        if not 0 <= index < len(self._steps):
            raise IndexError(f"no step {index}")
        self._files = copy.deepcopy(self._checkpoints[index])
        del self._steps[index:]
        del self._checkpoints[index:]

    def steps(self) -> list[Step]:
        return list(self._steps)

    def cleanup(self) -> None:
        self._files, self._steps, self._checkpoints = {}, [], []
        self._started = False

    def _execute(self, cmd: str) -> tuple[str, str, int]:
        if ">" in cmd and cmd.strip().startswith("echo"):
            text, path = cmd.split(">", 1)
            self._files[path.strip()] = " ".join(shlex.split(text)[1:]) + "\n"
            return "", "", 0

        try:
            parts = shlex.split(cmd)
        except ValueError as e:
            return "", str(e), 2
        if not parts:
            return "", "", 0
        prog, args = parts[0], parts[1:]

        if prog == "ls":
            return "\n".join(sorted(self._files)) + "\n", "", 0
        if prog in ("touch", "mkdir"):
            for a in args:
                self._files.setdefault(a.rstrip("/") + ("/" if prog == "mkdir" else ""), "")
            return "", "", 0
        if prog == "rm":
            targets = [a for a in args if not a.startswith("-")]
            if any(t in ("*", "/", ".") for t in targets):
                self._files.clear()
                return "", "", 0
            missing = [t for t in targets if t not in self._files]
            for t in targets:
                self._files.pop(t, None)
            if missing:
                return "", f"rm: {missing[0]}: No such file or directory\n", 1
            return "", "", 0
        if prog == "cat":
            out, err = "", ""
            for a in args:
                if a in self._files:
                    out += self._files[a]
                else:
                    err += f"cat: {a}: No such file or directory\n"
            return out, err, 1 if err else 0
        if prog == "echo":
            return " ".join(args) + "\n", "", 0
        return f"(fake) ran: {cmd}\n", "", 0
