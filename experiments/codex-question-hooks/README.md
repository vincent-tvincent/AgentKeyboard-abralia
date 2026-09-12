# Codex question hook probe

This bounded probe checks which Codex lifecycle/tool hooks reach an installed
client. It records only event/session/turn/tool identifiers, timestamps, question
counts and coarse response kinds. It never connects to Abralia, HID or a GUI,
and never returns a policy decision or forwards question/answer text.

From the repository root, preview the exact project-local hook definitions:

```sh
.venv/bin/python -B experiments/codex-question-hooks/probe.py show \
  --project /path/to/project --output /path/to/output/probe.jsonl
```

Replace `show` with `install` to append the managed block to that project's
`.codex/config.toml`, preserving other entries. The output directory is created
on the first observed event. Codex requires reviewing and trusting new hook
definitions before they run; the installer does not change trust settings.
Use the installed client's supported hook review/reload flow.

The probe matches all local tool calls so a normal shell command can serve as a
positive control when a question tool produces no hook. Do not interpret an
empty log as unsupported question observation until that control is observed.
Tool acceptance is not an answer. Check blocking and asynchronous questions,
resolution/cancellation, and turn boundaries separately on GUI and TUI.

Remove the probe afterward with the same arguments and the `remove` action.
An edited managed block is preserved for manual review. Logs are retained.

The terminal backend can now consume this journal alongside registered Codex
session logs. The probe itself still only records metadata. See the
[backend observer documentation](../../abralia/desktop/BACKEND.md#host-configuration-and-inspection)
for automatic question notifications, source limitations and the separate
hardware acceptance step.

```sh
.venv/bin/python -B -m unittest discover -s experiments/codex-question-hooks
```

Apache-2.0; see the repository-root `LICENSE.md`.
