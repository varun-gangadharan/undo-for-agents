# Undo for Agents — Demo Pitch

## 60-second pitch script

“Agents can run code, but when they make a mistake, there is no Undo. A bad cleanup can erase state needed to recover.

Undo for Agents gives them time travel. Archil forks a whole running machine in about three seconds—not just a Git working tree.
Every time the agent touches the computer, we save a snapshot first. The agent runs one command per step, so every action is rewindable.

In **Task**, type: `In /workspace, create demo.txt containing "I should survive", then list the directory.` Click **Run**. `demo.txt` appears.

Now type: `Clean up the project directory`. Click **Run**; the agent wipes the files. On that cleanup card, click **⏪ Rewind to before this step**. `demo.txt` returns.

Undo doesn't try to reverse what the agent did. It jumps back to a snapshot from before it happened.

Next, branch one checkpoint into agent swarms: explore fixes in parallel and keep the best branch.”

## Pre-demo checklist

- [ ] Activate the project virtual environment in the terminal that owns it; do not create or modify a virtual environment during the demo.
- [ ] Confirm `.env` is present with `ARCHIL_API_KEY` (and `ARCHIL_REGION` if required) plus `ANTHROPIC_API_KEY`.
- [ ] Start with `ENGINE=archil` and pre-load the Streamlit page so the first visible interaction is the task prompt, not startup.
- [ ] Verify the backup video is downloaded locally, opens, and is queued at the same setup-task screen.
- [ ] Keep this CLI launch fallback copied and ready if the browser tab needs to be reopened: `ENGINE=archil streamlit run app.py`

## Likely judge questions

1. **Why not Git?**  Git tracks selected filesystem changes and requires commits; this preserves the sandbox state immediately before an agent command, including uncommitted workspace changes, so recovery does not depend on the agent having made a good commit.

2. **What does a fork cost?**  It is an Archil sandbox operation, so the monetary cost depends on the Archil plan and sandbox lifetime. It takes about three seconds in our integration; production should set a checkpoint budget and benchmark its workload.

3. **What happens to old sandboxes?**  Rewind stops the broken live sandbox but does not delete it, because Archil cannot delete a parent while forked children exist. Cleanup deletes every tracked sandbox in child-before-parent order.

4. **How is this safe?**  Commands run in an isolated sandbox, and a checkpoint exists before a risky action. That makes recovery possible, but it is not a substitute for normal production controls such as secret scoping, network policy, command allowlists, and approval gates.

5. **How would it scale to swarms?**  Fork one known-good checkpoint into multiple sandboxes, give each agent a different proposed approach, score their outputs, and promote the winning branch. Checkpoint lifecycle management and per-branch budgets keep the fan-out bounded.
