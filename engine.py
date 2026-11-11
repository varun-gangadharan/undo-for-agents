"""ArchilEngine: the real Engine, backed by an Archil cloud sandbox.

Job:
- start(): create an Archil sandbox (ARCHIL_API_KEY, ARCHIL_REGION from .env).
- run(cmd): fork the current sandbox to save a checkpoint (time it as fork_s),
  then run cmd in the live sandbox and return a Step.
- rewind(index): fork checkpoint[index] into a new live sandbox, stop the old
  live one, and drop steps and checkpoints > index.
- cleanup(): delete every sandbox this engine created, children before parents.

Archil refuses to delete a sandbox that still has forked children, and every
checkpoint is a fork of a live sandbox. So nothing is deleted mid-session:
superseded sandboxes are only stopped, and cleanup() deletes in reverse creation
order (a child is always created after its parent).

Must satisfy contracts.Engine so it can replace FakeEngine without other changes.
"""
from __future__ import annotations

import os
import time
from uuid import uuid4
from typing import Any

from dotenv import load_dotenv

from contracts import Step


def diff_files(
    before: dict[str, str] | None, after: dict[str, str]
) -> tuple[list[str], list[str], list[str]]:
    """Return (added, removed, modified) paths. With no baseline, nothing is reported."""
    if before is None:
        return [], [], []
    added = sorted(after.keys() - before.keys())
    removed = sorted(before.keys() - after.keys())
    modified = sorted(p for p in after.keys() & before.keys() if after[p] != before[p])
    return added, removed, modified


class ArchilEngine:
    """An Engine implementation that saves Archil sandbox forks as checkpoints."""

    _RISKY_COMMAND_MARKERS = ("rm", "mv", ">", "git reset", "pip uninstall")
    _HASH_CMD = (
        "find /workspace -type f -not -path '*/__pycache__/*' -not -path '*/.pytest_cache/*' "
        "-exec md5sum {} + 2>/dev/null"
    )

    def __init__(self, name: str = "undo-for-agents") -> None:
        # Load credentials before importing/initializing the SDK client.
        load_dotenv()
        self._name = name
        self._session_name: str | None = None
        self._current: Any | None = None
        self._checkpoints: list[Any] = []
        self._steps: list[Step] = []
        self._sandboxes: dict[str, Any] = {}
        self._fork_serial = 0
        self.track_files = True
        self.initial_file_hashes: dict[str, str] | None = None

    def start(self) -> None:
        if self._current is not None:
            raise RuntimeError("ArchilEngine is already started")

        # Import lazily so importing this module does not require the SDK to be
        # configured until the real engine is actually used.
        import archil

        self._session_name = f"{self._name}-{uuid4().hex[:8]}"
        sandbox = archil.create_sandbox(name=self._session_name)
        self._track(sandbox)
        try:
            sandbox = self._ensure_running(sandbox)
            # The sandbox's default working directory exists before our
            # workspace does; do not set cwd until after this command succeeds.
            result = sandbox.exec("mkdir -p /workspace")
            if result.exit_code != 0:
                raise RuntimeError(self._command_error("mkdir -p /workspace", result))
        except Exception:
            self._dispose(sandbox)
            raise

        self._current = sandbox
        self._checkpoints = []
        self._steps = []
        self._fork_serial = 0
        self.initial_file_hashes = self._hash_files(sandbox) if self.track_files else None

    def run(self, cmd: str) -> Step:
        current = self._wake()
        index = len(self._steps)
        checkpoint, fork_s = self._checkpoint_for(cmd, current, index)

        started = time.perf_counter()
        result = current.exec(cmd, cwd="/workspace")
        duration_s = time.perf_counter() - started
        step = Step(
            index=index,
            command=cmd,
            stdout=self._truncate(result.stdout),
            stderr=self._truncate(result.stderr),
            exit_code=result.exit_code,
            duration_s=duration_s,
            fork_s=fork_s,
        )
        if self.track_files:
            step.file_hashes = self._hash_files(current)
            if step.file_hashes is not None:
                step.added, step.removed, step.modified = diff_files(self._last_hashes(), step.file_hashes)
        self._store_checkpoint(index, checkpoint)
        self._steps.append(step)
        return step

    def rewind(self, index: int) -> None:
        self._require_current()
        if not 0 <= index < len(self._steps):
            raise IndexError(f"no step {index}")

        checkpoint = self._settle(self._checkpoints[index])
        self._checkpoints[index] = checkpoint
        replacement = checkpoint.fork(name=self._fork_name(f"rewind-{index}"))
        self._track(replacement)
        replacement = self._ensure_running(replacement)

        old_current, self._current = self._current, replacement
        discarded = self._checkpoints[index + 1 :]
        # Checkpoint `index` is kept: it is the state before the next command.
        self._checkpoints = self._checkpoints[: index + 1]
        self._steps = self._steps[:index]
        for sandbox in [old_current, *discarded]:
            self._stop(sandbox)

    def steps(self) -> list[Step]:
        return list(self._steps)

    def cleanup(self) -> list[str]:
        """Delete every sandbox, children first. Returns ids that could not be deleted."""
        self._current = None
        self._checkpoints = []
        self._steps = []
        remaining = list(reversed(self._sandboxes.values()))
        for attempt in range(3):
            if attempt:
                time.sleep(2)
            failed = []
            for sandbox in remaining:
                self._stop(sandbox)
                try:
                    sandbox.delete()
                except Exception:
                    failed.append(sandbox)
            remaining = failed
            if not remaining:
                break
        self._sandboxes = {sb.id: sb for sb in reversed(remaining)}
        return [sb.id for sb in remaining]

    def sandbox_ids(self) -> list[str]:
        """Ids of every sandbox this engine created, children first."""
        return list(reversed(self._sandboxes))

    def _checkpoint_for(self, cmd: str, current: Any, index: int) -> tuple[Any, float]:
        if index < len(self._checkpoints):
            # Right after rewind(index) the kept checkpoint already matches the live state.
            return self._checkpoints[index], 0.0
        if self._should_fork(cmd) or not self._checkpoints:
            started = time.perf_counter()
            checkpoint = current.fork(name=self._fork_name(f"checkpoint-{index}"))
            self._track(checkpoint)
            fork_s = time.perf_counter() - started
            # Only the live sandbox should run; a stopped checkpoint can still be forked.
            try:
                checkpoint = checkpoint.stop(wait=False)
            except Exception:
                pass
            return checkpoint, fork_s
        return self._checkpoints[-1], 0.0

    def _store_checkpoint(self, index: int, checkpoint: Any) -> None:
        if index == len(self._checkpoints):
            self._checkpoints.append(checkpoint)

    def _last_hashes(self) -> dict[str, str] | None:
        for step in reversed(self._steps):
            if step.file_hashes is not None:
                return step.file_hashes
        return self.initial_file_hashes

    def _hash_files(self, sandbox: Any) -> dict[str, str] | None:
        try:
            result = sandbox.exec(self._HASH_CMD)
        except Exception:
            return None
        hashes = {}
        for line in (result.stdout or "").splitlines():
            digest, _, path = line.partition("  ")
            if path:
                hashes[path.removeprefix("/workspace/")] = digest
        return hashes

    def _fork_name(self, label: str) -> str:
        name = f"{self._session_name or self._name}-{label}-{self._fork_serial}"
        self._fork_serial += 1
        return name

    def _track(self, sandbox: Any) -> None:
        self._sandboxes.setdefault(sandbox.id, sandbox)

    def _should_fork(self, cmd: str) -> bool:
        mode = os.getenv("CHECKPOINT_MODE", "all").lower()
        if mode not in {"all", "risky"}:
            raise ValueError("CHECKPOINT_MODE must be 'all' or 'risky'")
        return mode == "all" or any(marker in cmd for marker in self._RISKY_COMMAND_MARKERS)

    @staticmethod
    def _ensure_running(sandbox: Any) -> Any:
        # Archil 0.21's create_sandbox() and fork() default to wait=True, so
        # they return running sandboxes. Lifecycle calls return a new Sandbox
        # object, therefore callers must retain the returned value.
        status = getattr(sandbox, "status", None)
        if status == "running":
            return sandbox
        if status == "paused":
            return sandbox.resume()
        return sandbox.start()

    @staticmethod
    def _truncate(value: Any) -> str:
        return (value or "")[:4000]

    @staticmethod
    def _command_error(cmd: str, result: Any) -> str:
        stderr = getattr(result, "stderr", "")
        return f"{cmd!r} failed with exit code {result.exit_code}: {stderr}"

    def _wake(self) -> Any:
        """Return the live sandbox, resuming it if Archil paused it for being idle (30 min)."""
        current = self._require_current().refresh()
        if current.status != "running":
            current = self._ensure_running(self._settle(current))
        self._current = current
        return current

    def _require_current(self) -> Any:
        if self._current is None:
            raise RuntimeError("call start() first")
        return self._current

    @staticmethod
    def _settle(sandbox: Any, timeout_s: float = 30.0) -> Any:
        """Wait out an in-progress stop or pause so the sandbox can be forked or resumed."""
        deadline = time.monotonic() + timeout_s
        while getattr(sandbox, "status", None) in ("stopping", "pausing") and time.monotonic() < deadline:
            time.sleep(0.5)
            sandbox = sandbox.refresh()
        return sandbox

    @staticmethod
    def _stop(sandbox: Any) -> None:
        if sandbox is None or getattr(sandbox, "status", None) == "stopped":
            return
        try:
            sandbox.stop()
        except Exception:
            pass

    def _dispose(self, sandbox: Any) -> None:
        self._stop(sandbox)
        try:
            sandbox.delete()
            self._sandboxes.pop(sandbox.id, None)
        except Exception:
            pass


def purge_project_sandboxes(prefix: str = "undo-for-agents-", max_passes: int = 15) -> tuple[int, list[str]]:
    """Stop and delete every sandbox whose name starts with prefix, from any session.

    Archil has no parent links in its API, so deletion retries in passes: each pass
    removes the sandboxes with no remaining children. Returns (deleted, leftover names).
    """
    import archil

    load_dotenv()
    gone = {"deleting", "deleted"}
    remaining = [sb for sb in archil.list_sandboxes() if (sb.name or "").startswith(prefix) and sb.status not in gone]
    return delete_children_first(remaining, max_passes)


def delete_children_first(remaining: list[Any], max_passes: int = 15) -> tuple[int, list[str]]:
    """Stop and delete sandboxes, retrying passes until parents no longer have children."""
    for sb in remaining:
        if sb.status not in {"stopped", "stopping", "exited", "failed"}:
            try:
                sb.stop(wait=False)
            except Exception:
                pass
    deleted = 0
    for attempt in range(max_passes):
        if attempt:
            time.sleep(2)
        failed = []
        for sb in remaining:
            try:
                sb.delete()
                deleted += 1
            except Exception:
                failed.append(sb)
        remaining = failed
        if not remaining:
            break
    return deleted, [sb.name for sb in remaining]

