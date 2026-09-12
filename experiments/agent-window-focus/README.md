# Agent window focus experiment

Focus an existing Codex GUI task, an exact Ghostty window/tab/split surface,
or a uniquely marked VS Code window.
This is a bounded macOS experiment, not a production integration or a universal
terminal adapter. Python 3.11+ uses only its standard library. Ghostty requires
macOS AppleScript support (1.3+) and enabled `macos-applescript`.

No keyboard hardware, Codex API credentials, backend, or Abralia installation
is needed. The scripts do not send keystrokes, read terminal contents, resume
CLI tasks, start an app-server, change configuration, or take writer ownership.
They store no target registry or logs. Focus commands change desktop focus;
`codex` can launch the registered GUI application if it is closed. Ghostty
commands require an already running application and never create terminals.

## Self-contained VS Code window experiment

The VS Code demo creates a uniquely marked workspace under `/private/tmp`,
opens it in a new VS Code window using the documented `--new-window` CLI, and
retains the marker internally. It activates Finder to put the target in the
background, raises that exact VS Code window through macOS Accessibility, and
checks that VS Code is frontmost with the marked window as its main window.
You do not copy or supply a window ID.

Preview without creating files or changing focus:

```sh
python3 vscode_focus_demo.py --dry-run
```

Run the bounded experiment:

```sh
python3 vscode_focus_demo.py --delay 1
```

Success is `VSCODE_WINDOW_FOCUS_VERIFIED`. The temporary workspace path is
included in the JSON result and is not removed while VS Code still has it open.
Close the demo window normally when finished; `/private/tmp` is temporary host
state.

This requires Accessibility permission for the application that launches the
script. It does not alter that permission, open System Settings, send keys,
read editor or terminal contents, or interact with the Codex extension. A
permission denial is an error, not a reason to select another window.

The result proves exact VS Code **workspace-window focus only**. It does not
prove which Codex-extension conversation is selected. A future VS Code
companion or extension-supported task identity is still required for that
claim.

## Self-contained iTerm2 session experiment

The iTerm2 demo uses iTerm2's official Python API rather than generic UI
automation. It creates a new window, retains its window/tab/session IDs in
memory, activates Finder, observes iTerm2's native focus-loss events, then
reactivates the retained session and verifies both the focus events and the
selected IDs. You do not copy or supply an ID.

iTerm2 must already be running with **Settings > General > Magic > Enable
Python API server** enabled. Install the separate client in an isolated
environment and run:

```sh
python3 -m venv /tmp/abralia-iterm2-focus-venv
/tmp/abralia-iterm2-focus-venv/bin/python -m pip install -r requirements-iterm2.txt
/tmp/abralia-iterm2-focus-venv/bin/python iterm2_focus_demo.py --delay 1
```

The first external client connection may also require an iTerm2 Python API
authorization decision. Handle that prompt directly in iTerm2. The script does
not change the setting or answer the prompt, and its entire authentication and
focus run fails closed after 20 seconds rather than waiting indefinitely.
The iTerm AI/OpenAI configuration is a different feature and does not enable
the Python API server used by this experiment.

Success is `ITERM2_SESSION_FOCUS_VERIFIED`. The test opens a normal default
shell but sends it no text or commands. The created window remains open for
inspection and can be closed normally when finished.

This result proves exact iTerm2 **window/tab/session focus**. It does not prove
which Codex CLI conversation is running in a session. Production registration
must associate the Codex task ID with the retained iTerm2 session ID and clear
that association when the process/session ends.

The optional `iterm2==2.22` client is maintained by the iTerm2 project and is
GPL-2.0-or-later; it remains a separate runtime dependency and no third-party
source is copied into Abralia. See [PyPI](https://pypi.org/project/iterm2/)
and the [iTerm2 Python API](https://iterm2.com/python-api/).

Run commands below from this directory, using a separate control terminal.

## Codex GUI

Copy the target task's deep link from the app. Substitute its UUID below:

```sh
python3 focus_demo.py codex 'codex://threads/<TASK-UUID>' --dry-run
python3 focus_demo.py codex 'codex://threads/<TASK-UUID>' --delay 3
```

During the delay, switch to another application. Check whether the GUI comes
forward **and shows the exact intended conversation**. Repeat with another
task in the same project. The result is `DISPATCHED_UNVERIFIED`: `/usr/bin/open`
returning successfully proves neither foreground focus nor task selection.
There is no private GUI inspection, UI injection, or automatic readback here.

## Ghostty CLI window, tab, or split

List native identities without collecting titles, working directories, or
conversation contents:

```sh
python3 focus_demo.py ghostty-list
```

To identify a specific surface, run this from the control terminal, then click
the desired Ghostty pane during the delay. It can already be running Codex:

```sh
python3 focus_demo.py ghostty-current --delay 3
```

Copy its `terminal_id`, return to the control terminal, and run:

```sh
python3 focus_demo.py ghostty-focus '<TERMINAL-ID>' --delay 3
```

An optional expected task ID is only a label for your manual check:

```sh
python3 focus_demo.py ghostty-focus '<TERMINAL-ID>' --expect-thread '<TASK-UUID>' --delay 3
```

`SURFACE_FOCUS_VERIFIED` means Ghostty reports itself foreground and reports
the requested terminal as focused in its front window's selected tab. This is
a point-in-time check, not a focus lock. **It does not prove which Codex task
is running there.** Check the conversation yourself; do not enter verification
text into the focused agent. The script never asks for input after focusing.

`--dry-run` is also supported by `ghostty-focus`. macOS may ask whether the
invoking application may automate Ghostty. If the bounded call times out while
you inspect that prompt, grant permission if desired and rerun manually. Do
not bypass a denial; Automation permission and Ghostty's own setting must allow
the operation. This experiment does not need Accessibility-based keystrokes.

## Validation checklist and limits

Use two tasks in the same project and record expected task, target ID,
foreground application, observed surface, manual topic match, and latency.
Do not treat exit code zero alone as an end-to-end pass.

- Alternate between two GUI tasks while another app is foreground.
- Alternate CLI targets across windows, tabs, and splits.
- Reorder or rename tabs; ID-based targeting should still select the same pane.
- Test a minimized window and another macOS Space; record failures honestly.
- Close a target: its missing ID must error without selecting another pane.
- End a CLI task but keep its terminal open: surface focus may still succeed,
  but topic verification must fail. There is no automatic stale-task detection.

Automatic task-to-process-to-surface registration, process-exit/TTY-reuse
guards, remote sessions, multiplexers, Terminal, and iTerm2 adapters remain
outside this demo. A future integration must validate that live association
before claiming `canFocus` for a task. This script never guesses by title,
project directory, or tab position.

## Offline checks

```sh
python3 -B -m unittest discover -s . -p 'test_*.py' -v
```

Tests mock native commands: they do not move windows or establish a live focus
result. Native compilation and live checks are separate verification steps.

Interface references: [Codex task deep links](https://learn.chatgpt.com/docs/reference/commands#supported-links),
[Ghostty AppleScript](https://ghostty.org/docs/features/applescript), and the
[VS Code CLI](https://code.visualstudio.com/docs/configure/command-line).

Abralia-authored code and documentation: Apache-2.0; see [LICENSE.md](../../LICENSE.md).
