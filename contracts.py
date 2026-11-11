"""Shared types every module agrees on. Change only with the whole team."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class Step:
    index: int
    command: str
    stdout: str
    stderr: str
    exit_code: int
    duration_s: float
    fork_s: float  # time spent forking the checkpoint taken before this step


class Engine(Protocol):
    def start(self) -> None:
        """Boot the sandbox. Call once before run()."""

    def run(self, cmd: str) -> Step:
        """Checkpoint the current state, then run cmd and record it as the next Step."""

    def rewind(self, index: int) -> None:
        """Restore the state from BEFORE step `index` ran and drop steps >= index."""

    def steps(self) -> list[Step]:
        """All steps run so far, in order."""

    def cleanup(self) -> None:
        """Tear down the sandbox and every checkpoint."""
