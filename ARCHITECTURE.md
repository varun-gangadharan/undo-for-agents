# Undo for Agents: Architecture

```
 ┌──────────────┐   task, rewind clicks
 │    app.py    │  (Streamlit UI)
 └──────┬───────┘
        │ run_agent(engine, task, on_step)
        ▼
 ┌──────────────┐   "run this bash command"
 │   agent.py   │  (Claude tool-use loop)
 └──────┬───────┘
        │ engine.run(cmd) / engine.rewind(i)
        ▼
 ┌──────────────────────────────────────┐
 │ Engine (contracts.py)                │
 │   FakeEngine (fake_engine.py, local) │
 │   ArchilEngine (engine.py, real)     │
 └──────┬───────────────────────────────┘
        ▼
 ┌──────────────┐  fork before   ┌───┐┌───┐┌───┐
 │ live Archil  │ ─each step───▶ │ 0 ││ 1 ││ 2 │ checkpoints
 │   sandbox    │ ◀─rewind(i)─── └───┘└───┘└───┘
 └──────────────┘  fork ckpt i
```

## Files

**contracts.py**: The shared rulebook. `Step` is one record of a command the agent
ran (what it typed, what came back, how long it took). `Engine` lists the five
methods every engine must have. Because everyone codes against this file, the UI,
the agent and the real engine can be built at the same time by different people.

**fake_engine.py**: A pretend engine that runs entirely in memory. It keeps a
dictionary of fake files and copies it before every command, so `rewind` really
does bring deleted files back. Use it to build and demo the agent and UI with no
API keys or internet.

**engine.py**: The real engine. It talks to Archil, which gives us a small cloud
computer (a microVM). Before each command it *forks* the machine, a fast copy
saved as a checkpoint. To rewind, it forks the checkpoint for that step and makes
the copy the new live machine.

**agent.py**: The "brain". It sends the task to Claude along with a `bash` tool.
Claude decides which command to run, the agent runs it through the engine, sends
the output back, and repeats until Claude says it's done or hits `max_steps`.

**app.py**: The web page (Streamlit). You type a task, watch each step appear, and
click "Rewind to here" on any step to undo everything from that step onward.
