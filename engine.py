"""ArchilEngine: the real Engine, backed by an Archil cloud sandbox.

Job:
- start(): create an Archil sandbox (ARCHIL_API_KEY, ARCHIL_REGION from .env).
- run(cmd): fork the current sandbox to save a checkpoint (time it as fork_s),
  then run cmd in the live sandbox and return a Step.
- rewind(index): fork checkpoint[index] into a new live sandbox, discard the old
  live one, and drop steps and checkpoints >= index.
- cleanup(): delete the live sandbox and every checkpoint.

Must satisfy contracts.Engine so it can replace FakeEngine without other changes.
"""
from __future__ import annotations

from contracts import Step


class ArchilEngine:
    def start(self) -> None:
        raise NotImplementedError

    def run(self, cmd: str) -> Step:
        raise NotImplementedError

    def rewind(self, index: int) -> None:
        raise NotImplementedError

    def steps(self) -> list[Step]:
        raise NotImplementedError

    def cleanup(self) -> None:
        raise NotImplementedError
