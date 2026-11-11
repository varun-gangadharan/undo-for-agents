# Undo for Agents — Demo Pitch

## 60-second pitch script

“Agents can write code and run commands, but when they make a mistake there is no Undo button. One bad cleanup command can erase the exact state the agent needed to recover.

Undo for Agents gives agents time travel. The key insight is Archil: it can fork a whole running machine in about three seconds, not just a Git working tree. Before an agent command runs, we save a machine checkpoint.

Here is the live demo. In the **Task** box, type exactly: `In /workspace, create a file named demo.txt containing "I should survive", then list the directory. Do not delete anything.` Then click **Run**. We now have a checkpointed setup step and `demo.txt` is in the directory.

Now replace the task with exactly: `Clean up the project directory` and click **Run**. The agent removes the file—the timeline shows the destructive command and its output.

But that was a bad decision. On that cleanup card, click **⏪ Rewind to before this step**. The timeline returns to the earlier state, and the file is back. If needed, type `List the project directory` in **Continue from here** and click **Continue** to prove it.

Under the hood, each command runs against a sandbox while its pre-command Archil fork is retained as a checkpoint; rewind forks that checkpoint back into the live machine.

Today this is undo for one agent. Next, those same checkpoints can branch into agent swarms: several agents explore different fixes from one safe point, then we keep the best branch.”

## Pre-demo checklist

- [ ] Activate the project virtual environment in the terminal that owns it; do not create or modify a virtual environment during the demo.
- [ ] Confirm `.env` is present with `ARCHIL_API_KEY` (and `ARCHIL_REGION` if required) plus `ANTHROPIC_API_KEY`.
- [ ] Start with `ENGINE=archil` and pre-load the Streamlit page so the first visible interaction is the task prompt, not startup.
- [ ] Verify the backup video is downloaded locally, opens, and is queued at the same setup-task screen.
- [ ] Keep this CLI launch fallback copied and ready if the browser tab needs to be reopened: `ENGINE=archil streamlit run app.py`

## Likely judge questions

1. **Why not Git?**  Git tracks selected filesystem changes and requires commits; this preserves the sandbox state immediately before an agent command, including uncommitted workspace changes, so recovery does not depend on the agent having made a good commit.

2. **What does a fork cost?**  It is an Archil sandbox operation, so the monetary cost depends on the Archil plan and sandbox lifetime. The app surfaces fork latency per step; the pitch target is roughly three seconds, and production use should set a checkpoint budget and benchmark the chosen workload.

3. **What happens to old sandboxes?**  On rewind, the old live sandbox is stopped and deleted; later discarded checkpoints are also deleted. Cleanup tears down the live sandbox and all tracked forks, in child-before-parent order.

4. **How is this safe?**  Commands run in an isolated sandbox, and a checkpoint exists before a risky action. That makes recovery possible, but it is not a substitute for normal production controls such as secret scoping, network policy, command allowlists, and approval gates.

5. **How would it scale to swarms?**  Fork one known-good checkpoint into multiple sandboxes, give each agent a different proposed approach, score their outputs, and promote the winning branch. Checkpoint lifecycle management and per-branch budgets keep the fan-out bounded.
