# ⏪ Undo for Agents

Agents make mistakes. Mine can time-travel.

![Undo for Agents screenshot](docs/screenshot.png)

## How it works

- **Checkpoint:** before each command, Archil forks the whole microVM, taking about 3 seconds.
- **Rewind:** fork that checkpoint into a fresh sandbox and discard the later timeline.
- **Agent:** Claude operates through `run_bash`, with Keenable `web_search` when it needs outside information.

```
 ┌──────────────┐   task, rewind clicks
 │    app.py    │  (Streamlit UI)
 └──────┬───────┘
        │ run_agent(engine, task, on_step)
        ▼
 ┌──────────────┐   run_bash / web_search
 │   agent.py   │  (Claude + Keenable)
 └──────┬───────┘
        │ engine.run(cmd) / engine.rewind(i)
        ▼
 ┌──────────────────────────────────────┐
 │ Engine (contracts.py)                │
 │   FakeEngine (local demo)             │
 │   ArchilEngine (cloud microVM)        │
 └──────┬───────────────────────────────┘
        ▼
 ┌──────────────┐  fork before   ┌───┐┌───┐┌───┐
 │ live Archil  │ ─each step──▶  │ 0 ││ 1 ││ 2 │ checkpoints
 │   sandbox    │ ◀─rewind(i)──  └───┘└───┘└───┘
 └──────────────┘
```

## Why Snapshot The Whole VM Instead Of Git

Git only captures files that are committed, while an agent can also change generated files, installed packages, processes, and the working tree. A microVM fork restores the complete execution environment at the instant before a command ran.

## Setup

```bash
uv venv --python 3.12
source .venv/bin/activate
uv pip install -r requirements.txt
cp .env.example .env
streamlit run app.py
```

The Streamlit app starts in local fake-engine mode. Set `ENGINE=archil` to use your Archil credentials from `.env` and run against a real sandbox.

For a terminal-only fallback, run:

```bash
python scripts/cli_demo.py
```

## Future Work

Branch one checkpoint into a swarm of parallel agents.

Built at the Archil hackathon.
