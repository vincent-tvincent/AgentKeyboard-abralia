<p align="center">
  <img src="./docs/Abralia_logo_with_text.png" alt="Abralia logo" width="900">
</p>

# Abralia

## Give Your Keyboard an Agent Mode - Without Tradeoff

***Don't be afraid of flashing our firmware! Your V3 8K keeps its existing RGB
modes, VIA and Keychron Launcher configuration, and the 8K polling option—plus
new agent controls and cool lighting effects!***

**Your agents can call you through your keyboard. Pick up a task with a key,
mute interruptions, and see pending calls as floating lights.**

<p align="center"><img src="./docs/agent-mode-overview.jpg" alt="Abralia Agent Mode on a Keychron V3 8K, with colored task slots and highlighted navigation controls" height="300"></p>

*Different agents. Different colors. Pick one with a key.*

Abralia is an open-source firmware and desktop software project that turns a
compatible per-key RGB keyboard into a physical interface for coding agents.
Agents occupy colored slots, request attention through the keyboard, and let
you pick up a task, mute interruptions, or browse tasks using physical controls.

**Current application: a Codex keyboard interface on macOS.** The repository
includes a Python backend, Codex plugin, automatic session observation and
keyboard interaction. The
[macOS control panel](abralia/desktop/gui/README.md) scans supported keyboards,
remembers your choice, starts one shared backend and lets you enable or mute
projects. **Connect Codex** installs the bundled skill, MCP tools and named hooks;
Codex's hook-trust review remains a separate user step.

Abralia is compatibility-first and additive by design. Its native firmware
keeps Keychron RGB effects 0–24 and keeps VIA/Keychron Launcher, encoder, and
8K report-rate support enabled while adding independent per-key brightness,
effect 25, and an opt-in Host Interaction Mode.

It does not require permanently replacing ordinary keys with unused F13–F24
bindings. Unbound controls continue to behave normally, Host Interaction
bindings are volatile, and stale or disconnected host sessions are designed
to restore ordinary input behavior.

The firmware and standalone desktop RGB/input APIs can also be used independently
of the agent backend.

## Quick start: macOS + Codex

**User guides**

- **[Keyboard user guide](docs/user-guide/README.md)** — shortcuts, effects, GIF demonstrations and model-specific controls.
- **[Controls and shortcuts](docs/user-guide/controls.md)** — activation, navigation, pickup, mute and key behavior.
- **[Lighting effects](docs/user-guide/effects.md)** — task colors, breathing, notification animations and fog reminders.
- **[Simulator guide](tools/keyboard-simulator/README.md)** — setup, design inputs and GIF export.
- **[App setup guide](abralia/desktop/gui/README.md)** — keyboard selection, the shared backend, project muting and Codex plugin setup.

**Developer guides**

- **[Device discovery and profile authoring](abralia/desktop/DEVELOPER_CLI.md)** — scan interfaces, probe capabilities, and draft/validate keyboard profiles.
- **[Firmware build and porting](abralia/firmware/qmk-userspace/README.md)** — pinned upstream setup, additive firmware variants and model-specific builds.
- **[Desktop RGB API](abralia/desktop/README.md)** and **[Host Interaction API](abralia/desktop/HOST_INTERACTION_API.md)** — scenes, device adapters, shared HID ownership and physical input bindings.
- **[Backend and MCP guide](abralia/desktop/BACKEND.md)** — agent tools, slot ownership, notifications, lifecycle and testing.
- **[Contributor rules](AGENTS.md)** and **[Claude Code instructions](CLAUDE.md)** — required reading, architecture boundaries and portability requirements.

**Get started**

You need Codex, the Abralia macOS app, and a supported keyboard running the
matching [Host Interaction firmware](abralia/firmware/qmk-userspace/README.md).
Select RGB **effect 25** on the keyboard. Abralia checks the firmware when it
connects; the app does not flash it for you. To build the app from source, follow
the [macOS preview build steps](abralia/desktop/gui/README.md#develop-and-package).

1. **Open `Abralia.app` and choose your keyboard.** Abralia remembers the device
   and starts its backend automatically. Check that the app says **Backend running**.
2. **Open Integrations → Connect Codex.** This installs the bundled Abralia skill,
   MCP tools and named lifecycle hooks. Review and trust the six **Abralia:** hooks in
   **Codex Settings → Hooks**; installation does not approve them automatically.
3. **Load the tools in Codex.** If your current task cannot see Abralia's tools,
   restart Codex and resume that task after installation.
4. **Ask the agent to join:**

   > Enable Abralia for this task and register yourself.

   The agent enables its current project if needed, claims its own slot and
   reports its page/F-key. Installing the plugin alone does not register agents.
   You can also enable folders through **Projects → Add project** and control
   project notifications there.
5. **Try the keyboard.** On the V3 8K, double-tap the physical **Pause** position
   to enter Agent Mode, then press the agent's occupied **F-key** to open it.
   Ask **“Send a test notification through Abralia.”** **Print Screen** picks up
   the call and **Scroll Lock** mutes it while Agent Mode is active.

For navigation, hold Pause for about **0.8 seconds**. Arrows browse agents/pages,
Enter opens the previewed task, and **Escape cycles arrival order / project
grouping**. After 15 seconds of navigation inactivity, the knob returns to page
browsing. Double-tap Pause to return to ordinary typing.

Closing the app window keeps Abralia running in the background. Choose **Quit**
from the app or Dock menu to stop it and release the keyboard.

**No slot or notification?** Confirm the backend is running, the agent has
explicitly registered, and Abralia's hooks are trusted. Answer native Codex
questions in Codex's UI; numbered page keys are not answer shortcuts.
For terminal/editor pickup, check the
[supported navigation routes](abralia/desktop/BACKEND.md#cli-terminals-and-vs-code-windows).
See the [app guide](abralia/desktop/gui/README.md) for full setup and troubleshooting.

### A quick look at the app

Pick your keyboard, choose which projects can interrupt you, and connect Codex
from one control panel. These screenshots show the current interface with example
projects and device data. The app follows your system's light or dark appearance;
the screenshots below adapt too. Full-size links are available for both themes.

**Choose your keyboard once.** Abralia finds supported devices, remembers your
selection and starts the backend. Closing the window keeps it running; Quit
releases the keyboard.

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="./docs/assets/gui/keyboards-dark.png">
    <img src="./docs/assets/gui/keyboards.png" alt="Abralia keyboard screen showing the remembered Keychron V3 8K and the running backend" width="900">
  </picture>
</p>
<p align="center"><a href="./docs/assets/gui/keyboards.png">Light screenshot</a> · <a href="./docs/assets/gui/keyboards-dark.png">Dark screenshot</a></p>

**Keep the work, mute the interruptions.** Add the project folders you want to
use, see their task counts, and mute a project's notifications while its agents
continue working.

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="./docs/assets/gui/projects-dark.png">
    <img src="./docs/assets/gui/projects.png" alt="Abralia Projects screen with notifications enabled for one example project and muted for another" width="900">
  </picture>
</p>
<p align="center"><a href="./docs/assets/gui/projects.png">Light screenshot</a> · <a href="./docs/assets/gui/projects-dark.png">Dark screenshot</a></p>

**Connect your agents.** Install or update Abralia's bundled skill, MCP
tools and named hooks from Integrations. Review hook trust in Codex, then ask
each task to join—installing the plugin does not register agents automatically.

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="./docs/assets/gui/integrations-dark.png">
    <img src="./docs/assets/gui/integrations.png" alt="Abralia Integrations screen showing the installed Codex plugin, available runtime and hook activity" width="900">
  </picture>
</p>
<p align="center"><a href="./docs/assets/gui/integrations.png">Light screenshot</a> · <a href="./docs/assets/gui/integrations-dark.png">Dark screenshot</a></p>

## Design and test without a keyboard

The standalone [keyboard simulator](tools/keyboard-simulator/README.md) runs in
your browser with a grayscale workbench around the colored LEDs. Try physical
gestures, send calls from test agents, import JSON timelines or LED frames, plug
in a Python effect, and export your design as a GIF. It uses the same host
controller and renderer as Abralia, with model geometry supplied by device profiles.

```sh
.venv/bin/python tools/keyboard-simulator/simulator.py serve
```

See the [simulator setup and examples](tools/keyboard-simulator/README.md) for
dependencies and custom profiles. It runs independently of the app and does not
connect to a physical keyboard.

For complete usage instructions, use the linked
[user guide](docs/user-guide/README.md):
[controls](docs/user-guide/controls.md),
[lighting and effects](docs/user-guide/effects.md), and
[keyboard-specific mappings](docs/user-guide/keyboards/README.md).
Six narrated simulation GIFs illustrate the actions alongside written steps.
Shared behavior is documented once; each model has its own page.

## Task colors and notifications

### One color per task

Each registered task gets an identity color and a stable logical slot. F1–F12
show one page of twelve slots; the same task keeps its color as it works,
requests attention, or moves to another position. The color identifies the
task, while animation and highlighting indicate attention and selection.

Project grouping gives related agents similar hues. Switching back to arrival
order restores their original individual colors. Colors are generated
procedurally without a fixed palette capacity; finite RGB values can eventually
repeat, while slot IDs and ownership remain distinct.

<details open>
<summary><strong>See the task slots up close</strong></summary>

<p align="center"><img src="./docs/agent-slot-detail.jpg" alt="Distinct task colors on F1–F5 above the ordinary white typing background" height="300"></p>

*The colored F-slots identify separate tasks. Preview a task with the navigation
keys or optional knob, then press Enter to open it; an occupied F-key opens it
directly.*

</details>

### When several agents need your attention

A new call starts with a yellow-green arrival animation, then transitions and
breathes in the calling task's color. If it remains unpicked, the broad color
contracts into a floating fog orb. Each task contributes one orb, so several
pending callers share the keyboard surface in their own colors.

<p align="center"><img src="./docs/multi-agent-fog.jpg" alt="Five task colors on the F-row and overlapping notification fog across the typing area" height="300"></p>

*Five agents' pending notifications sharing the keyboard surface. Each fog orb
belongs to a task; overlapping orbs blend their colors.*

The soft fog envelopes overlap while smaller collision cores deflect one
another. Saturated centers keep task colors recognizable. Fog can drift through
the typing, navigation and arrow regions; active control highlights remain
visible above it. Arrival animations are queued independently of pending calls,
so one unanswered task does not block the next task's visual introduction.

Pick up a task with Print Screen, its F-key, or an Enter-confirmed preview to
remove its existing orb. A later notification can create a new reminder;
retries do not duplicate or restart an existing one. Scroll Lock mutes the
current call's strong announcement and leaves a quiet orb. Ten-minute agent
mutes hide affected orbs while their age continues.

By default, an orb holds for two minutes and fades over the next minute, within
its notification's lifetime. Fading only clears the visual reminder; it does
not answer a question or mark the task viewed. Direct clicks inside Codex are
not currently tracked as exact-task viewing events. See the
[notification fog guide](abralia/desktop/BACKEND.md#notification-fog) for timing
settings, simulation, image previews and the bounded hardware demo.

## What works today

- **One backend owns the keyboard.** A serialized device worker handles
  RGB and input through `SharedRawHidSession`; MCP bridges use a private local
  socket and never open HID themselves. Hardware and simulated modes are available.
- **Agents register through the Codex plugin when asked.** They can report progress and
  request attention. The host can also register existing project tasks without
  waking them, release individual slots, or release all slots.
- **Stable task identity with movable placement.** Each allocation has a slot ID,
  ownership token and identity color. Its displayed page/F-key is a separate
  mapping, so rearranging the keyboard does not assign an agent another task's slot.
- **Notifications and conversation pickup.** Incoming calls appear without first
  selecting an agent. Pickup opens its registered Codex task; mute quiets the call.
  The Codex observer detects pending native questions and their replies, and
  requests attention when a registered task's turn finishes.
- **Paged navigation and attention controls.** Twelve positions per page, optional
  knob browsing, direct page jumps, selected-slot brightness breathing, ten-minute
  individual mutes and an “only this agent” mode are implemented. Page indicators
  appear briefly after a page change and remain available in knob selection mode.
- **Manual gap closing.** Hold an empty F-key in active Agent Mode: the whole gap
  brightens and flashes, then later agents compact forward across pages while
  retaining their identities and state.
- **Arrival or project sorting.** Escape in keyboard navigation switches ordering
  and cached color palettes. New registrations follow the current policy; the
  policy and both palettes survive live-session recovery.
- **Bounded cleanup and recovery.** Backend shutdown releases volatile bindings
  and attempts to restore the saved RGB scene. A surviving verified MCP bridge
  can restore allocations and identity colors after a backend restart with fresh
  tokens. Calls, questions and orbs are not restored; host-only registrations
  must be repopulated.

The Codex notification/pickup workflow and selected navigation/lighting behaviors
have been exercised on reference hardware, including whole-gap closing and
selected-slot brightness breathing. A five-task live MCP trial confirmed the
multi-agent fog display, stronger center colors and orb removal after pickup.
Automated checks cover ownership, retries, paging, input routing, rendering,
collision and color-mixing math, independent notification lifetimes, recovery,
project setup and real STDIO MCP clients.

Agents use semantic operations: `enable_self`, `acquire_slot`, `release_slot`, `set_slot_state`,
`set_notification`, `report_question`, `clear_question`, and `get_status`.
Optional `set_notification_animation` adds a short effect over the task color;
agents without an immediate idea simply keep the default. `show_keyboard_frame`
can explain a keymap after pickup, with Escape to close it and
`clear_keyboard_frame` for explicit withdrawal. Highlighting does not remap keys.
The backend owns animation, input bindings and policy. See the
[agent tool contract](abralia/desktop/BACKEND.md#agent-tools) for ownership and
retry rules; agents do not receive raw HID or native approval execution.

**Optional extensions:** native question-panel focus and keyboard execution of
answers/approvals are not required for the notification, navigation and attention
workflow. They are not currently implemented; answer questions in Codex's native
UI. Broader GUI settings, launch at login and other harness
adapters are separate future enhancements. The numbered keys used for page
navigation are not answer shortcuts.
Codex session observation currently depends
on local client data formats and needs compatibility checks when the client changes.

## Terminal setup for development

For the packaged macOS workflow, open Abralia, choose your keyboard, use
**Integrations → Connect Codex**, review its hooks in Codex, then add your project
folders. See the [app setup guide](abralia/desktop/gui/README.md) and the
[standalone plugin bundle](abralia/desktop/plugin-bundle/README.md).

The separate terminal workflow remains available for development:

From the repository root, using Python 3.11+:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e 'abralia/desktop[backend]'
.venv/bin/abralia-backend enable-project --project /path/to/project
.venv/bin/abralia-backend serve --project /path/to/project \
  --profile builtin:keychron-v3-8k-ansi-encoder-effect25 --mode simulated
```

Use the same existing project path in each command. Enablement installs only
Abralia's project-local MCP configuration and skill; Codex must trust the project
and load its MCP tools. Simulated mode does not access the keyboard or open tasks.

For a physical keyboard, install the matching
[Host Interaction firmware](abralia/firmware/qmk-userspace/README.md), select RGB
effect 25, and start the backend with `--mode hardware`. The backend does not
flash firmware. See the [backend guide](abralia/desktop/BACKEND.md) for setup,
host registration/release commands, appearance settings, recovery and limitations.

## Reference keyboard controls

These labels refer to physical positions on the V3 8K profile, independently of
their VIA keycode mappings. Task controls operate while Agent Mode is active.
Use the [complete controls guide](docs/user-guide/controls.md) for mode precedence
and illustrated sequences, or the [model index](docs/user-guide/keyboards/README.md)
to locate the controls on another keyboard.

| Gesture | Action |
| --- | --- |
| Double-tap Pause | Enter or leave Agent Mode |
| Occupied F-key | Select and open that task; a pending call is picked up |
| Green Print Screen / red Scroll Lock | Pick up / mute the incoming call |
| Hold Pause | Toggle the temporary navigation-key layer |
| Escape in keyboard navigation | Cycle arrival order / project groups and their color palettes |
| Page Up / Up, Page Down / Down in keyboard navigation | Previous / next page |
| Left / Right in keyboard navigation | Preview the previous / next occupied slot on the page |
| Home / End in keyboard navigation | Preview the first / last task across all pages |
| Enter with a task preview | Select and open that task |
| Knob press, when available | Switch between agent and page browsing; Enter confirms a preview |
| Number-row keys in agent-selection mode | Jump to occupied pages |
| Delete / Insert in keyboard navigation | Toggle an individual ten-minute mute / “only this agent” |
| Hold Delete in keyboard navigation | Clear individual mutes |
| Hold an empty F-key | Close that gap across later pages |
| Escape while focused, outside keyboard navigation | Leave task focus and restore the background |

The [backend guide](abralia/desktop/BACKEND.md#physical-interaction) describes
the complete key mapping, timers and lighting codes. Inactive typing retains
ordinary input. The backend preserves the keyboard's own brightness limit.

Navigation keys disarm after 15 seconds without navigation activity, also returning
the knob to page browsing. Independently entered knob agent-selection mode returns
to page browsing after 60 seconds without selection activity. Selected-task
background color and Escape's focus-exit cue hold for 15 seconds,
then fade together over five seconds. That lighting fade does not end task focus
or answer a pending question. These timers and the idle white background are
host settings, separate from the keyboard's brightness ceiling.

Muted slots become dimmer and less saturated. Delete and Insert act on the
previewed task first, then the focused task, then the incoming caller. Ending
“only this agent” mode restores everyone and clears earlier individual mutes.

## Find the right documentation

For building or flashing firmware, using the desktop APIs, looking up default
key mappings, or reproducing an experiment, go to the
[documentation navigation page](docs/README.md).

## Keyboard support

| Keyboard | Status |
| --- | --- |
| Keychron V3 8K ANSI encoder | Reference hardware; firmware and desktop interaction exercised physically |
| Original Keychron V3 ANSI / ANSI encoder | Experimental firmware targets and bundled profiles; hardware validation pending |
| Other keyboards | Require a matching firmware port, profile and hardware validation |

The reference firmware retains Keychron RGB effects 0–24, VIA/Launcher, normal
encoder behavior and the 8K report-rate setting. Configuration/readback and
compatibility testing do not constitute a measurement of continuous report cadence.
Firmware images are model-specific; follow the [build guide](abralia/firmware/qmk-userspace/README.md).

Contributions for other keyboards and layouts are welcome. The device profile
describes physical controls and RGB geometry; a knob is optional. Additional
harness adapters, more appearance settings and Windows/Linux app packages are
future contribution areas. The existing macOS app already handles keyboard
selection, project management and integration setup.

Before contributing, read the [contributor and agent instructions](AGENTS.md)
for required documentation, additive firmware porting, portable device support
and architecture boundaries. [CLAUDE.md](CLAUDE.md) loads the same guidance for
Claude Code.

For profile authors, **[`abralia-dev`](abralia/desktop/DEVELOPER_CLI.md)** can scan
HID interfaces before a profile exists, query a selected firmware protocol,
generate an explicitly incomplete draft, and validate/export a completed profile.
USB discovery supplies matching fields; geometry, LED mapping and physical
verification remain separate steps.

<details open>
<summary><strong>RGB experiments: a Codex-inspired logo and the original Fog Orb</strong></summary>

<p align="center"><img src="./docs/demo_image_banner.jpg" alt="A Codex-inspired logo rendered across the Keychron V3 8K" height="300"></p>

*A Codex-inspired logo rendered across the keyboard using Abralia's RGB API.*

![Standalone host-driven Fog Orb animation moving across the Keychron V3 8K](./docs/fog-orb-animation-demo.gif)

*The original Fog Orb is a standalone effect-25 animation rendered through the
desktop RGB API. It supplied the visual starting point for the task-notification
orbs shown above; this GIF demonstrates the standalone scene.*

Both examples are host-rendered scenes. The
[RGB experiments guide](experiments/desktop-rgb-physical-validation/README.md#fog-orb-animation)
explains how to run the animation with the matching profile.

</details>

## What the Abralia firmware adds to a Keychron keyboard

**Let your computer paint the keys and give them new jobs—while keeping the
keyboard features you already use.**

The firmware adds a channel for live per-key lighting and temporary physical
controls. Your V3 8K keeps its existing RGB modes, VIA/Keychron Launcher
configuration, normal knob behavior and 8K setting. You can use the RGB API on
its own for lighting experiments, or connect the agent backend for task calls
and navigation. Agent-specific behavior lives on the computer, so changing the
fog animation or agent workflow does not require reflashing the keyboard.

Two firmware variants are available:

| Variant | What it provides |
| --- | --- |
| `led_only_not_interactable` | Effect-25 RGB control and the local awaiting halo; no host input bindings |
| `abralia_host_interaction` | RGB control plus temporary key/knob bindings for the agent backend |

Use the Host Interaction variant for the full agent workflow. The
[firmware overview](abralia/firmware/README.md) and
[model-specific build guide](abralia/firmware/qmk-userspace/README.md) explain
the available targets and installation.

- **Independent relative per-key brightness.** Keys can be bright, dim or off
  relative to each other while the keyboard brightness remains a master ceiling.
  The current firmware normalizes a frame against its brightest key; a uniformly
  dim host frame is not a guaranteed absolute percentage of hardware brightness.
- **Host-driven full-keyboard scenes and animation.** The desktop API can send
  complete 87-key frames for smooth gradients, status surfaces, progress,
  notifications, game layouts, and visual experiments.
- **Guarded frame updates with automatic recovery.** Complete frames are
  committed atomically and require a live host lease. If updates stop, the
  firmware leaves the stale frame and returns to its local awaiting state.
- **A firmware-native awaiting halo.** When effect 25 is not displaying a host
  scene, the keyboard can render a low-power breathing halo locally without
  continuous computer-side frame streaming.
- **Generic Host Interaction controls.** The
  `abralia_host_interaction` variant lets a trusted desktop broker temporarily
  bind matrix keys, knob press, and both encoder directions to opaque action
  IDs without hard-coding agent commands into firmware.
- **Per-control `CAPTURE` or `MIRROR` routing.** A temporary binding can either
  suppress the ordinary key action or emit a Host Interaction event while
  preserving normal behavior. Unbound controls remain ordinary keyboard
  controls.
- **Bounded volatile lifetimes.** Bindings support session, TTL, and one-shot
  lifetimes. Host-forced activation uses renewable bounded leases, and no Host
  Interaction command writes EEPROM.
- **Explicit entry and fail-safe exit.** Double Pause toggles Host Interaction
  Mode, while heartbeat loss or clean session release clears volatile input
  state and restores normal behavior.
- **Compatibility remains the baseline.** Keychron RGB effects 0–24, VIA,
  Keychron Launcher, encoder mappings, the original USB identity, DFU recovery,
  and the V3 8K report-rate feature remain available.

## Repository layout

- `abralia/firmware/` contains the QMK External Userspace firmware.
- `abralia/desktop/` contains the Python 3.11+ RGB/input APIs, shared backend,
  macOS GUI app, Codex plugin/MCP integration and optional project-local setup.
- `tools/keyboard-simulator/` contains the independent browser design tool and GIF exporter.
- `docs/user-guide/` contains shared controls/effects and separate keyboard-model guides.
- `experiments/` contains bounded hardware and protocol experiments.

<details open>
<summary><strong>RGB and Host Interaction controller architecture</strong></summary>

![Abralia RGB and Host Interaction controller architecture](./docs/RGB_control_design.drawio.png)

Editable source: [RGB_control_design.drawio](./docs/RGB_control_design.drawio)

</details>

## License

Abralia uses separate licenses for firmware and host-side software:

- Abralia firmware: GPL-3.0-or-later
- Desktop software, host tools, experiments, protocol material, and
  documentation: Apache-2.0

Upstream and third-party material retains its original notices and licenses.
See [LICENSE.md](LICENSE.md) for the exact scope.
