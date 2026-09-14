# Abralia Codex plugin bundle

This marketplace contains the Abralia plugin for the macOS Abralia app. It adds
agent tools, a skill and clearly named **Abralia** lifecycle hooks. The app owns
the keyboard backend and must be running; this bundle does not start another
keyboard service or require a separate Python installation.

Use **Connect Codex** in the matching Abralia app to install the runtime and
plugin, then review the hooks in Codex. Ask the agent **“Enable Abralia for this
task”** to enable its verified current project and claim its own slot, or enable
the project folder in the app first. Agents activate only after an explicit
user request; installing the plugin or opening a project does not opt in a task.
Closing Abralia's window keeps it running. Explicit Quit disconnects the keyboard.

For manual plugin installation after the app has registered its runtime, extract
this complete bundle into a permanent directory and run:

```sh
codex plugin marketplace add /path/to/abralia-codex
codex plugin add abralia@abralia
```

Review/trust the plugin hooks in Codex Settings. Installation is separate from
hook trust. Start a new task after installation so Codex loads the plugin tools.
Removing the plugin uses `codex plugin remove abralia@abralia`; the app retains
device and project preferences. Avoid enabling a second Abralia installation
or the older experimental project-local hooks at the same time.

The skill disables implicit invocation. Once you request activation, the agent
keeps that permission through the task and follow-up questions. Disabling the
project or releasing its slot in the app is not permission to re-enable it;
activation then needs a new explicit request. Hooks never enroll projects or
allocate slots. If the app is offline, `enable_self` reports saved enrollment
separately from slot acquisition.

Version 0.1.0 requires the Abralia desktop build with shared project enrollment
and the `mcp`/`codex-hook` runtime entry points. Older control-panel-only previews
do not provide this runtime contract. Windows and Linux packaging are not part
of this first macOS bundle.

Source and documentation: Apache-2.0; see [LICENSE](plugins/abralia/LICENSE).
