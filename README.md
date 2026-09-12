<p align="center">
  <img src="./docs/Abralia_logo_with_text.png" alt="Abralia logo" width="900">
</p>

# Abralia

## Give your keyboard an Agent Mode

<p align="center"><img src="./docs/demo_image_banner.jpg" alt="Keychron V3 8K displaying Abralia's Codex-style hero frame" height="300"></p>

Abralia is an open-source firmware and desktop software project that turns a
compatible per-key RGB keyboard into a physical interface for coding agents.
Agents occupy colored slots, request attention through the keyboard, and let
you pick up a task, mute interruptions, or browse tasks using physical controls.

**Current application: a Codex keyboard interface on macOS.** The repository
includes a terminal backend, project-local MCP tools and skill, automatic Codex
session observation, and keyboard interaction. Setup and administration currently
use terminal commands.

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

## What works today

- **One terminal backend owns the keyboard.** A serialized device worker handles
  RGB and input through `SharedRawHidSession`; MCP bridges use a private local
  socket and never open HID themselves. Hardware and simulated modes are available.
- **Agents register through project-local MCP.** They can report progress and
  request attention. The host can also register existing project tasks without
  waking them, release individual slots, or release all slots.
- **Stable task identity with movable placement.** Each allocation has a slot ID,
  ownership token and identity color. Its displayed page/F-key is a separate
  mapping, so rearranging the keyboard does not assign an agent another task's slot.
- **Notifications and conversation pickup.** Incoming calls appear without first
  selecting an agent. Pickup opens its registered Codex task; mute quiets the call.
  The Codex observer can detect pending native questions and their replies.
- **Paged navigation and attention controls.** Twelve positions per page, optional
  knob browsing, number-row page indicators, selected-slot lighting, ten-minute
  individual mutes and an “only this agent” mode are implemented.
- **Manual gap closing.** Hold an empty F-key in active Agent Mode: the whole gap
  brightens and flashes, then later agents compact forward across pages while
  retaining their identities and state.
- **Bounded cleanup and recovery.** Backend shutdown releases volatile bindings
  and attempts to restore the saved RGB scene. A surviving verified MCP bridge
  can restore allocations after a backend restart with fresh tokens. Host-only
  registrations must be repopulated after restart.

The Codex notification/pickup workflow and selected navigation/lighting behaviors
have been exercised on reference hardware, including whole-gap closing and
selected-slot brightness breathing. Automated checks cover ownership, retries,
paging, input routing, rendering, recovery, project setup and real STDIO MCP clients.

**Optional extensions:** native question-panel focus and keyboard execution of
answers/approvals are not required for the notification, navigation and attention
workflow. They are not currently implemented; answer questions in Codex's native
UI. A settings GUI, autostart and other harness adapters are separate future
enhancements. The numbered keys used for page navigation are not answer shortcuts.
Codex session observation currently depends
on local client data formats and needs compatibility checks when the client changes.

## Try the backend

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

| Gesture | Action |
| --- | --- |
| Double-tap Pause | Enter or leave Agent Mode |
| Occupied F-key | Select and open that task; a pending call is picked up |
| Green Print Screen / red Scroll Lock | Pick up / mute the incoming call |
| Hold Pause | Toggle the temporary navigation-key layer |
| Knob press, when available | Switch between agent and page browsing; Enter confirms a preview |
| Number-row keys in agent-selection mode | Jump to occupied pages |
| Delete / Insert in keyboard navigation | Toggle an individual ten-minute mute / “only this agent” |
| Hold Delete in keyboard navigation | Clear individual mutes |
| Hold an empty F-key | Close that gap across later pages |
| Escape while focused | Leave task focus and restore the background |

The [backend guide](abralia/desktop/BACKEND.md#physical-interaction) describes
the complete key mapping, timers and lighting codes. Inactive typing retains
ordinary input. The backend preserves the keyboard's own brightness limit.

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
harness adapters and a desktop settings interface are also future contribution areas.

<details open>
<summary><strong>What the Abralia firmware adds to a Keychron keyboard</strong></summary>

![Host-driven fog-orb animation moving across the Keychron V3 8K](./docs/fog-orb-animation-demo.gif)

*The fog orb is a host-driven effect-25 scene rendered through Abralia's
desktop RGB API, not a permanently stored keyboard effect.*

- **Independent relative per-key brightness.** Keys can be bright, dim or off
  relative to each other while the keyboard brightness remains a master ceiling.
  The current firmware normalizes a frame against its brightest key; a uniformly
  dim host frame is not a guaranteed absolute percentage of hardware brightness.
- **Host-driven full-keyboard scenes and animation.** The desktop API can send
  complete 87-key frames for smooth gradients, status surfaces, progress,
  notifications, game layouts, and visual experiments such as the animation
  above.
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

</details>

## Repository layout

- `abralia/firmware/` contains the QMK External Userspace firmware.
- `abralia/desktop/` contains the Python 3.11+ RGB/input APIs, terminal backend,
  project-local MCP integration and macOS-first Keychron effect-25 adapter.
- `experiments/` contains bounded hardware and protocol experiments.

<details>
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
