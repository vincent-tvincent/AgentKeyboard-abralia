# Abralia macOS control panel

An Electron/React frontend for the Python backend. This first macOS version:

- Lists connected USB devices matching the three implemented Keychron profiles.
- Saves an explicit keyboard choice and uses its identity on backend restart.
- Starts and manages its backend automatically when the app opens.
- Combines enabled projects on one keyboard and shows their task counts.
- Mutes/unmutes each project's keyboard attention without stopping its agents.
- Installs/removes the bundled Abralia Codex plugin and offers manual hook TOML.

The Python helper and full backend are included in the app bundle; normal use
does not require a terminal startup command. One shared backend owns the keyboard
for all enabled projects. The Codex plugin supplies its skill, semantic MCP tools
and clearly named Abralia lifecycle hooks.

## Use

Open `Abralia.app`. With a saved connected keyboard, the app starts its backend
automatically. Open **Projects → Add project** to enable a folder. Only enabled
projects can register tasks or send events to the shared keyboard service.
After plugin setup, you can instead ask the agent **“Enable Abralia for this
task.”** Its `enable_self` tool enables its verified current project and claims
only its own slot. Installing the plugin or enabling a folder alone does not
tell agents to activate.

If no keyboard has been selected, choose one with **Use keyboard**; saving the
choice also starts the backend. Selection is remembered even
while disconnected; no other device is silently substituted. A serial number
identifies the device when available; otherwise the exact HID path is used.
The app uses the selected keyboard's implemented profile when starting its
backend. Descriptor discovery does not establish that Abralia firmware is
installed; the backend checks that before opening a control session. Older
separately started backends are detected and labelled **Restart required**;
the app does not take over their keyboard connection.

Open **Projects** to turn notifications off or on. The setting persists until
changed. Agent execution, task slots and manual pickup remain available. New
automatic attention inherits the project mute, and unmuting does not replay old
arrival animations. Device choice and project policies are stored privately in
`~/Library/Application Support/Abralia/control`.

Open **Integrations → Connect Codex** to install the plugin from the bundled local
marketplace. Setup copies the complete packaged runtime and plugin into stable
per-user locations and invokes Codex's supported plugin manager. The installed
client must provide the `codex plugin` commands; setup reports a missing or
incompatible client instead of editing arbitrary global configuration.

Review/trust **Abralia:** hooks in Codex, then refresh the plugin or start a new
task so its tools are loaded. The app distinguishes installation, runtime
availability and hook events received. Installing a plugin does not trust its
hooks. **Remove plugin** uses the plugin manager and preserves app preferences.

**Copy manual hook TOML** is an alternative to plugin hooks. Do not enable both
routes or leave older experimental Abralia hooks active. The standalone release
[plugin bundle](../plugin-bundle/README.md) includes manual installation steps.

Use **Stop** to stop a backend launched by Abralia, and **Start** to run it again.
On macOS, the red close button hides the window and keeps the backend running.
Click Abralia in the Dock to reopen the same window. Choose **Quit** from its
Dock or application menu when you want to stop the app.
Quitting the app waits for that owned backend to release input bindings and
restore its saved RGB scene. A separately started terminal backend is reused
when compatible and remains running when the app closes. Quit an older
single-project backend before starting the shared app backend.

The bundled terminal backend is also directly runnable:

```sh
"/path/to/Abralia.app/Contents/Resources/backend/abralia-gui-host/abralia-gui-host" \
  serve --project /path/to/project \
  --profile builtin:keychron-v3-8k-ansi-encoder-effect25 --mode hardware
```

## Develop and package

From the repository root, prepare the existing Python package with the backend
extra and PyInstaller in `.venv`. Then, from this directory:

```sh
npm ci
npm run dev
npm test
npm run package:plugin
npm run package:mac
```

Node 22.12+ is required by the pinned Electron tooling. The development app uses
the repository's `.venv/bin/python`; `ABRALIA_PYTHON` can select another prepared
interpreter. Packaging builds the Python helper with PyInstaller, includes its
resources outside ASAR, and produces `out/Abralia-macos-<arch>-preview.zip`.
The build prints the temporary staged `Abralia.app` path too. App bundles are
staged outside sync folders so Finder/cloud metadata does not invalidate their
signatures. Relative Python library links remain internal to the app. The
packaged app uses only its bundled runtime. Packaging also produces
`out/Abralia-Codex-Plugin-<version>.zip`, with its hidden marketplace, skill,
hook and MCP files preserved.

This build is locally ad-hoc signed for testing. Public distribution needs
Developer ID signing and notarization. The current build target is macOS;
Windows/Linux packaging remains separate work.

For isolated tests, `ABRALIA_STATE_DIR`, `ABRALIA_RUNTIME_DIR`, and
`ABRALIA_GUI_USER_DATA` redirect control state, backend discovery and Electron
user data. `ABRALIA_MANAGED_MODE=simulated` keeps HID unopened. Set a separate
`CODEX_HOME` when testing real plugin-manager installation in isolation.

## Architecture

The sandboxed renderer uses typed management operations and a native folder
picker for project enrollment. Electron main owns
the bundled helper's process and NDJSON pipe. The helper owns the backend it
starts and can discover external services through private metadata. Management
commands pass through the existing serialized device worker, which remains the
sole HID owner for its keyboard. Project-root enrollment, native Codex task
context and per-project ownership checks route plugin calls. EOF runs graceful
owned-backend cleanup before the helper exits.

## License

Abralia UI and helper source: [Apache-2.0](../../../LICENSE.md).
See [third-party notices](THIRD_PARTY_NOTICES.md) for frontend dependencies.
