# Undo for Agents: Architecture

## Flow

```
Streamlit app or CLI
        |
        v
run_agent(engine, task)
        |
        +--> Claude tool call: run_bash --> engine.run(command)
        |
        +--> Claude tool call: web_search --> Keenable
        |
        v
Engine protocol
        |
        +--> FakeEngine: in-memory local demo
        |
        +--> ArchilEngine: Archil sandbox and fork checkpoints
```

`contracts.py` defines the `Engine` protocol: `start`, `run`, `rewind`, `steps`, and `cleanup`. Both engines return the same `Step` record: command, stdout, stderr, exit code, command duration, and checkpoint-fork duration.

## Interfaces

### Streamlit UI

`app.py` creates one engine per browser session. It uses `ENGINE=fake` by default so the interface works without cloud credentials; `ENGINE=archil` creates an `ArchilEngine`. The app renders a timeline of immutable command cards, exposes a rewind button for each step, reports average fork time, and resets the engine on demand. If the agent loop is unavailable, it offers a direct-command control instead.

### Agent loop

`agent.py` loads environment variables with `python-dotenv` and drives Claude through Anthropic's Messages API. Claude receives two tools:

- `run_bash` calls `engine.run` and sends the command result back to Claude.
- `web_search` calls Keenable and returns a shortened search context.

The UI limits an invocation to eight shell commands. Command output sent back to Claude is capped at 3,000 characters.

### Engines

`fake_engine.py` is a local in-memory implementation for UI development. It understands a small subset of shell commands and deep-copies its file dictionary before every command.

`engine.py` is the Archil implementation. It loads `.env`, creates a uniquely named sandbox, and creates `/workspace` before commands run there. Archil 0.21 creates and forks sandboxes with `wait=True` by default, so both operations return a running sandbox. Its fork operation briefly pauses a running source sandbox, snapshots it, resumes the source, and returns the child.

Before a command, `ArchilEngine` normally forks the current sandbox and records the fork duration in the new `Step`. `CHECKPOINT_MODE=all` is the default; `CHECKPOINT_MODE=risky` reuses the preceding checkpoint except for commands containing `rm`, `mv`, `>`, `git reset`, or `pip uninstall`. Command stdout and stderr are each truncated to 4,000 characters for the timeline.

`rewind(index)` forks the checkpoint from immediately before that step into a new running sandbox, disposes the former live sandbox, keeps the selected checkpoint, and drops later history. Archil prevents deletion of a fork parent before its children, so the engine tracks every sandbox it creates and cleans them up in reverse creation order. Cleanup ignores teardown errors so callers can always finish a session.

## Scripts

- `scripts/cli_demo.py` runs the real Archil engine interactively, with commands to rewind, continue, or quit.
- `scripts/try_engine.py` is a live smoke test: create `a.txt`, list it, delete the workspace, rewind, and verify that `a.txt` returns.
- `scripts/try_agent.py` exercises the agent loop against `FakeEngine`.
