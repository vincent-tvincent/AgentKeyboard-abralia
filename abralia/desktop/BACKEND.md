# Agent keyboard backend

The optional Python backend owns one effect-25 keyboard, receives semantic agent
reports through a local MCP bridge, and displays stable logical slots on paged
F1–F12 controls. It uses the [Apache-2.0 license](../../LICENSE.md).

The [macOS app](gui/README.md) runs a shared backend for explicitly enabled
project folders and installs the [Abralia Codex plugin](plugin-bundle/README.md).
Its plugin uses the bundled runtime, validates native task/project context and
routes all projects to one keyboard worker. Project mute and allocation ownership
are isolated per project. The terminal commands below retain the earlier
single-project workflow for development; do not run both integration routes
for the same task.

## Install and start

From the repository root, install into your chosen Python 3.11+ environment:

```sh
python -m pip install -e 'abralia/desktop[backend]'
abralia-backend serve --project /path/to/project \
  --profile builtin:keychron-v3-8k-ansi-encoder-effect25 --mode simulated
```

The process stays in the terminal until Ctrl-C or SIGTERM. Simulated mode never
opens HID or another application. For the reference V3 8K, select enabled RGB
effect 25 and use `--mode hardware` after installing the matching Host Interaction
firmware with single-tap capability. The backend itself does not flash firmware.
It refuses to preempt an existing firmware session or guarded RGB owner.

One worker owns the synchronous RGB/input controllers. The STDIO MCP bridges
connect to its private Unix socket and keep their connections alive without
model heartbeat calls. The backend suspends after device/effect loss; select
effect 25 and restart it to establish a fresh session. Old allocation tokens
are invalid after restart. Normal shutdown releases bindings and restores the
saved RGB state; a disconnected device may prevent restoration.

The automatic socket path is short enough for macOS. Use `--socket` on both
backend and clients to override it. The socket directory must be private to the
current OS user. This is a same-user local interface, not authentication against
malicious processes already running as that user.

## Enable only a selected project

```sh
abralia-backend enable-project --project /path/to/project
abralia-backend inspect-project --project /path/to/project
abralia-backend disable-project --project /path/to/project
```

Enable writes an `abralia_experiment` entry in the project's `.codex/config.toml`
and a local `.agents/skills/abralia/SKILL.md`. It never edits global Codex settings.
Existing unrelated settings and modified skills are preserved. Conflicting
Abralia entries require manual reconciliation. A management manifest records
only generated content needed for disable; it contains no transport credentials.
Running enable again refreshes an unchanged managed skill when the bundled
template changes, while preserving the MCP configuration and any user edits.

Codex must trust the project and refresh its MCP tools. If the tools do not
appear on the next turn, inspect `/mcp` and use the app's MCP restart control.
The code behind these commands is also available through
`abralia.backend.project.enable_project`, `inspect_project`, and `disable_project`
for a future settings UI.

## Agent tools

All mutations require an idempotency key. Reuse it only when retrying the same
arguments. All ownership checks use caller identity plus the returned slot token.
`slot_id` identifies an allocation, independently of its `display_position`.
Page/F-key placement comes from that position. Manual gap closing can move an
agent without changing its ID or token; use `get_status` for current placement
and `layout_revision`, rather than calculating a key from the ID. Acquisition
retries also refresh placement for a still-owned allocation. Release still ends
ownership; an ID reused afterward has a fresh token and cannot accept old calls.

| Tool | Purpose |
| --- | --- |
| `enable_self(label, idempotency_key, harness?)` | After an explicit user request, enroll the verified native current project if needed and acquire only this task's slot. No project-path argument. |
| `acquire_slot(label, idempotency_key, harness?)` | After an explicit user request, register the caller's harness and claim or recover its stable logical slot in an enabled project. |
| `release_slot(slot_token, idempotency_key)` | Clear that allocation, including its notification and question. |
| `set_slot_state(slot_token, state, idempotency_key, summary?, progress?)` | Report idle, progressing, error, action_requested, or completed without notifying automatically. |
| `set_notification(slot_token, enabled, idempotency_key, summary?, animation?)` | Request/withdraw one call, optionally with a short overlay. An update does not override mute or replay an existing onset. |
| `set_notification_animation(slot_token, animation, idempotency_key)` | Save an optional default for future calls, including observed questions. Null restores normal breathing; does not notify. |
| `show_keyboard_frame(slot_token, colors, idempotency_key, summary?)` | Request a static keymap guide through its own notification; pickup reveals it and Escape closes it. |
| `clear_keyboard_frame(slot_token, frame_id, idempotency_key)` | Withdraw the caller's exact pending or visible guide without affecting native questions. |
| `report_question(slot_token, question_id, kind, idempotency_key, options?, allow_other?)` | Prepare a call and native answer-key hints. Call before asking the native question. |
| `clear_question(slot_token, question_id, idempotency_key, outcome?)` | Restore question lighting/controls after answered, cancelled, withdrawn, skipped, or expired. |
| `get_status(slot_token?)` | Inspect readiness, identity, and only the caller's own allocation/feedback. |

`report_question` accepts `single_choice` or `free_text`. Options have unique
`id` and `label` fields and must exactly match the native question's order.
Single choice supports 1–8 options so a ninth shortcut can represent Other.
The tested client profile is Codex desktop; one native question is reported at
a time. Neither the MCP bridge nor the backend presents or answers it.
Native approvals, multi-select, and arbitrary UI focus monitoring are not exposed.

Activation requires an explicit user request to use Abralia for the task.
The plugin skill disables implicit invocation. `enable_self` can enroll the
verified current project and claim a slot; normal tools and hooks never enroll.
It enrolls the exact native workspace directory even when an ancestor folder is
already enabled, accepts no arbitrary project path, and reports `project_enabled`
separately from `slot_acquired` if the app is offline. For example, enabling
`/projects/work/app` does not silently reuse an enrolled `/projects` root.
Enable retries are scoped to the caller, arguments and enrollment generation
within the bridge lifetime; an old request cannot silently re-enable a disabled
project. A new explicit activation request uses a fresh key. User intent is an
instruction policy, not an agent-supplied boolean presented as proof.

Resolved shared-mode calls expose `project_context.native_workspace`, `enrolled_root`
and `match` (`exact` or `ancestor`) so a project's enrollment boundary cannot be
mistaken for the task's actual working directory. Ordinary status/event routing
can still find an enabled ancestor, and existing owned allocations remain usable
within that scope. A new registration requires exact workspace enrollment;
otherwise the backend returns `native_workspace_requires_exact_enrollment` and
does not create a slot, including for callers using an older MCP bridge.
Use the updated `enable_self`, or **Projects → Add project** with the exact folder,
then retry with a fresh activation idempotency key if a previous request was bound
to the ancestor. Existing allocations are not silently moved between project scopes;
release an incorrectly scoped allocation explicitly before registering it again.

Within an enabled project, the agent can register through `acquire_slot`, reporting `harness` as
`codex_desktop`, `codex_cli`, or `unknown`. The bridge reads its own task identity
from Codex's `_meta.x-codex-turn-metadata.thread_id`; task IDs and target URLs are
never model arguments. A desktop agent can then use task pickup and native
question hints without a per-task host command. For example:

```json
{"label": "Review input routing", "harness": "codex_desktop", "idempotency_key": "start-review"}
```

Acquisition returns the allocation and caller registration. Harness information
is explicitly `surface_source=agent_reported`; it does not prove current UI focus.
CLI registrations can navigate through a host-verified terminal attachment;
they do not receive desktop question hints. Omitted harness preserves an existing
registration; a new caller without it remains unknown. An unknown allocation
can be upgraded by acquiring with a harness and a new idempotency key. Changing
between known harnesses requires release and reacquisition.

### CLI terminals and VS Code windows

Direct F-key selection and incoming-call pickup use the same navigation route.
The bridge supplies native client process lifetime and allowlisted terminal
metadata, separately from model arguments. The backend attaches only the CLI
root task; subagents sharing its MCP process do not inherit its terminal pane.
Closed clients, reused process IDs, conflicting live clients, and stale
attachments cannot redirect another task's slot. A restart restores the slot's
placement, then establishes navigation again from the live bridge.

| Environment | Navigation capability |
| --- | --- |
| Terminal.app, iTerm2 (macOS) | Native tab/session matched to the CLI's terminal device. |
| Ghostty (macOS) | Exact terminal device matching when the installed AppleScript API exposes `tty`. Older versions can use an unambiguous single-terminal instance; multiple panes require the newer API. |
| WezTerm | Native pane selection through its existing local CLI/mux connection. |
| kitty | Native window selection through an already configured local remote-control socket. |
| Windows Terminal | Window-only targeting when the captured application process owns exactly one eligible native window. This does not select a tab. |
| VS Code | Window-only targeting when the captured application process owns exactly one eligible native window: macOS Accessibility, Windows native window APIs, or Linux X11. This does not select an integrated terminal or agent conversation. |

The routing layer includes macOS, Windows and Linux paths; the packaged Abralia
app is currently macOS-only. Windows/Linux native behavior needs testing on
those operating systems. Linux Wayland window activation, remote/multiplexed
sessions without an exact local association, and older Ghostty builds lacking a
usable target report unavailable. Abralia does not enable remote control,
change terminal settings, send keystrokes, or create replacement windows.
macOS Automation/Accessibility permissions and an emulator's own control settings
must already permit the chosen operation.

Inspect `allocation.navigation_target` for the provider, target specificity and
last pickup result. A dispatched request, a verified terminal surface, and a
verified conversation are distinct results. Native focus runs in a bounded
helper process so terminal delays do not stall keyboard rendering or heartbeat.
Rapid selection supersedes pending native requests and discards stale results.

Registration belongs to the allocation: bridge reconnects preserve it during
the existing cleanup grace period. A surviving bridge can also recover it after
a backend restart, as described below. Release or expired disconnection cleanup
requires acquisition again. A shared MCP bridge retains separate registrations
for separate harness-supplied task identities.

For compatibility, a host may still explicitly register a known desktop task:

```sh
abralia-backend register-desktop --project /path/to/project --thread-id <task-uuid>
```

Alternatively pass repeatable `--desktop-thread-id <task-uuid>` flags to `serve`.
This optional legacy path reports `surface_source=registered_by_host`. It is not
needed when the agent reports its harness, and does not detect UI focus. It
records pickup metadata only; use the following commands to allocate actual slots.

### Host registration and release

The host can register existing project tasks without starting model turns:

```sh
abralia-backend register-tasks --project /path/to/project --all
abralia-backend register-tasks --project /path/to/project --thread-id <task-uuid>
abralia-backend release-tasks --project /path/to/project --thread-id <task-uuid>
abralia-backend release-tasks --project /path/to/project --all
```

Repeat `--thread-id` to register/release several tasks. `--all` registration uses
the backend's local Codex index, restricted to unarchived user tasks whose working
directories are inside this project. Internal review/subagent sessions and other
projects are excluded. This adapter currently recognizes `cli`/`vscode` session
sources and the versioned `state_*.sqlite` index. It reads IDs, working directories
and display names, never the prompt-containing `title` column or conversation
contents. A missing display name gets a neutral task-ID label. Missing/unsupported
index data fails closed. Worktrees outside the project root require a future
catalog adapter that verifies their project association.

Registration is additive: existing agents keep their slots, colors, ownership and
state. New slots start with `registration_source=host`, `agent_attached=false` and
idle state. This means the task is registered, not that its model is running.
Normal observation can subsequently report its native state. Host responses never
include ownership tokens. Only that task's own MCP identity can adopt the slot
through `acquire_slot`; it keeps the slot and color and receives its token then.
Host-created slots stay registered when the admin command or an MCP connection
closes. Status reports current MCP connection evidence separately as
`agent_connected`; connection evidence is not a claim that the model is thinking.

Release can target another task's slot or every allocation in this backend,
including agent-created slots and pending recovery reservations. It clears their
calls/questions and invalidates tokens without stopping or deleting Codex tasks.
Other slots retain their positions. Repeating a completed request is safe: admin
operations use an idempotency key and backend epoch, and a retry cannot release
a replacement allocation. The CLI supplies both; custom IPC callers must preserve
them on retries. These operations exist only on the local admin channel, not the
agent-callable MCP tool list. Explicit later acquisition may allocate a slot again.

Unadopted host registrations are session-local. After a backend restart, rerun
registration to repopulate them without waking agents. A host-created slot with
a surviving verified agent bridge follows the existing live-client recovery
rules. Release is saved before acknowledgement so old recovery records cannot
resurrect released slots.

Missing identity returns `skipped`. A maintainer can configure a dedicated single-task test
connection using matching `--registered-thread-id` flags on backend and bridge;
do not use that fallback to claim several unidentified tasks are isolated.
Caller metadata and transport isolation must be checked for each supported host.

Results distinguish admission (`accepted`, `skipped`, `rejected`) from delivery
(`simulated`, `queued`, `ready`, `written`, `suspended`). New hardware mutations
return queued until the worker processes them; `device_delivery` describes the
device's last known output state, not delivery of that new mutation. A write or URL dispatch does not
prove that a person saw the display or that focus succeeded.

## Recover allocations after a backend restart

The backend remembers each recoverable caller's slot number, harness, label and
identity color. When the same MCP bridge reconnects and the original supported
Codex client process is still alive, it restores that allocation automatically.
The bridge's normal heartbeat reconnects without a model call. Pages and F-key
positions follow the restored display positions; IDs remain independent.
Recovery format 2 stores both fields. Format 1 is migrated by using each old ID
as its original display position, preserving the previous layout. Recovery
reserves IDs and positions separately and rejects duplicate positions.

Recovery requires both a private nonce retained by the live bridge and a matching
client process lifetime. The macOS detector recognizes the Codex/ChatGPT desktop
application ancestor or a terminal-attached Codex CLI process, checking PID,
process start time, executable and terminal. A terminal client inside the app
must still have its own process alive. Unknown ancestry, a closed original client
or a restarted bridge does not qualify for automatic recovery. This checks
process lifetime, not whether an individual GUI task tab or window is visible.

Saved assignments reserve their positions and colors for
`recovery_grace_seconds` (30 by default, configurable from 5 to 120). This window
starts after the backend listener is ready. Unclaimed reservations are invisible
and expire without creating ghost slots. The state file is `<socket-stem>.slots.json`
beside the private Unix socket, restricted to its owner. It contains stable
allocation metadata, process lifetime evidence and nonce digests, never allocation
tokens or raw bridge nonces. Acquisition and release are saved before acknowledgement;
an uncertain release is excluded from automatic recovery by the bridge.

Every restored allocation receives a fresh token. Models must use `get_status`
without an old token, or `acquire_slot` with a new idempotency key, before sending
new updates. Old mutation tokens remain invalid. Notifications, question content,
progress, summaries, focus, navigation and activation state are not restored from
disk. Restored slots start idle; the native observer may then rediscover a still
pending question and issue a current notification. Recovery is not guaranteed
to be silent when the client still has work awaiting the user.

`slot_recovery` in status reports pending/restored counts and storage errors;
the caller's tool result also reports `client_verified`. An invalid or unsafe
state file is preserved and automatic recovery fails closed. A stable allocation
change that cannot be saved for a verified recovery client is rejected with
`recovery_storage_unavailable`, rather than acknowledged and later forgotten.

When upgrading from a bridge without recovery support, reload the project's
Abralia MCP connection once (or restart Codex), restart the terminal backend and
register again. After that initial reload, backend-only restarts can recover
allocations while the original client and its bridge remain running. Reloading
the bridge itself always requires explicit registration again.

## Physical interaction

Keyboard navigation is the default; a rotary knob is optional. In active Agent
Mode, hold the profile-defined physical mode key for about 0.8 seconds to arm
navigation. Hold it again to disarm, or stop navigating for 15 seconds. Both
actions return the knob to page browsing and release its selection preview.
Leaving Agent Mode also disarms it. Selecting/picking up an agent does **not** disarm it.

| Key | Navigation action |
| --- | --- |
| Page Up / Up | Previous page, without wrapping. |
| Page Down / Down | Next page, without wrapping. |
| Left / Right | Previous/next occupied slot on the current page, skipping holes. |
| Home / End | First/last occupied agent across all pages. |
| Enter | Confirm the previewed agent; a pending call is picked up. |
| Escape | Cycle global arrival order / project groups, preserving arrival order inside each group. |

Page changes preserve the F-key position when occupied, otherwise use the first
occupied position on the new page. Movement only previews; it does not open a
conversation. Insert/Delete provide the timed attention controls described below.
Navigation cues have separate semantic color groups:

| Purpose | Keys | Default color |
| --- | --- | --- |
| Change page | Page Up / Page Down / Up / Down | Cyan `00BFFF` |
| Move between slots | Left / Right | Orange `FF9000` |
| Jump to an endpoint | Home / End | Violet `A060FF` |
| Change sorting policy | Escape, while navigation is armed | Violet `A040FF` |
| Confirm / answer | Enter | Yellow-green `A0FF00` |
| Cancel / quit | Escape, while its exit binding is effective | Red |
| Strong incoming-call controls | Print Screen: Pick Up / Scroll Lock: Mute | Pure green / red, unchanged |

The candidate retains its yellow-green/identity blend. These cues scale against
the existing frame reference and leave other keys alone. The three navigation
defaults remain subject to physical visual acceptance.

### Slot sorting and project colors

While keyboard navigation is armed, violet Escape cycles two persistent policies:

- **Arrival order:** agents appear in registration order. A new registration
  appends after the last position, even when an earlier release left a hole.
- **Project groups:** projects follow their first-arrival order, with registration
  order inside each project. A new agent joins the end of its project group and
  shifts later occupied positions forward when necessary, including across pages.

Switching policy also closes gaps. Release itself leaves the other positions
unchanged, and the existing hold-to-close-gap gesture remains available. Slot IDs,
ownership tokens, notifications, mutes and the selected agent survive movement;
only display positions change. Old F-key, Enter and sort releases cannot target
new occupants after a rearrangement. Sorting waits until startup recovery
reservations have been reconciled, and commits to disk before bindings change.

Each agent caches an individual color and a project color. Project mode uses
similar hues within a project, with modest hue, saturation and brightness
variation between agents. Returning to arrival order restores the exact original
individual colors. The policy, arrival sequence and both colors survive live
session recovery; legacy recovery files use their saved layout as the initial
order because they did not record historical registration times.

Colors are generated algorithmically, without a fixed palette capacity for
agents or projects. RGB has a finite number of values and human perception is
more limited, so large populations can have similar or repeated colors. Color
collisions never reject a registration; stable IDs and tokens identify agents.
`get_status` reports `sort_policy`, and the allocation includes `arrival_order`,
`individual_color`, `project_color` and the currently displayed `identity_color`.

Outside keyboard navigation, Escape retains its focus-exit behavior. A visible
keyboard guide keeps its own red Escape-to-dismiss binding. Sorting does not
change the strong green pickup/red mute controls or the keyboard brightness cap.

The physical mode key keeps firmware double-tap activation and host hold-to-toggle
navigation. Its captured single action does nothing; call controls use separate
Print Screen and Scroll Lock positions. Inactive typing keeps the firmware's
ordinary behavior. Hold timing comes from the existing VIA switch-matrix readback
through the same shared HID session, using the profile's fixed matrix position,
not an OS Pause keycode. Matrix values are transient and never logged. The device
worker polls while active; no second HID handle or firmware update is required.
The supplied Keychron builds already enable this VIA readback. A valid all-zero
reply alone does not prove physical input visibility; verify a real mode-key hold.

While navigation remains armed, its arrow keys and Enter navigate agents rather
than the native question panel. Hold the mode key again or allow the idle timer
to expire before using those arrow keys to answer. The separate knob selector
can remain active afterward, retaining Enter and page-number capture; confirm
the agent, press the knob back to pages, or let its 60-second idle timer expire
to release them. The native question itself remains pending. Agent MCP tools
cannot arm/disarm this mode or synthesize its input.

### Selection contrast and number-row pages

The highlighted candidate keeps its yellow-green/identity blend with slightly
reduced saturation (85% retained by default). It targets 30% more encoded
brightness than the other occupied slots. When the current frame has no headroom,
the renderer keeps the candidate within that frame's maximum and temporarily
reduces only the other occupied slots to at most roughly 77% of the candidate's
value. It never raises the keyboard's global brightness limit or dims unrelated
keys. The normal slot colors/values return when selection preview ends. Timed
mute dimming/desaturation is applied afterward, so a muted candidate remains dim.
In knob agent-selection mode, the candidate additionally breathes on HSV V,
cycling between 85% and 100% of its allowed value over two seconds. Other occupied
slots are capped against the breath's minimum and stay steady, retaining at least
the intended relative contrast before mute dimming. The main background and
keyboard brightness limit remain steady. Page-mode previews do not use this V
breath; only the currently previewed agent receives it.

A page change displays the populated pages on the physical number row: other
occupied pages are dim cyan and the current page is orange. Outside agent
selection this is an informational display only; digits keep their ordinary
input and the overlay disappears five seconds after the last page-navigation
action. In knob agent-selection mode, the display stays visible and occupied
page digits jump directly to their pages without opening or selecting a task.
Enter/F-key confirmation remains the separate pickup/selection action.

Keys `1`–`9`, then `0`, represent pages 1–10. For more than ten pages, the bank
containing the current page is shown (11–20, etc.); `page_display` in diagnostics
reports the exact mapping. Empty pages have no occupied cue or direct-jump
binding. An empty current page can still have an informational orange marker.
Binding history and selection/bank checks reject releases from an old mode,
changed bank or removed destination. Native question hints take precedence over
a passive overlay; while page keys are captured, page cues take precedence.

Knob agent selection returns to page browsing after **60 seconds without selection
activity**, as selected by the maintainer. Rotation, relevant navigation input and
number-page jumps renew the timer; model updates and heartbeat do not. The
15-second navigation-key timeout disarms that key layer independently. If the
knob selector is still active, its numbers/candidate remain until its own timeout
or explicit exit. Explicitly holding the mode key to disarm navigation also ends
the selector. Knob press is available while active and occupied, including after
focus or timeout, so it can always restart agent selection on an encoder model.

### Timed attention controls

While keyboard navigation is armed, Delete and Insert act on the previewed agent,
otherwise the focused agent, otherwise the current incoming caller. Their names
refer to fixed physical profile positions, not a search for assigned keycodes.

| Gesture | Action |
| --- | --- |
| Tap Delete | Mute that agent for ten minutes; tap again on that muted agent to restore it early. |
| Hold Delete for about 1.2 seconds | Clear all individual agent mutes. The release does not perform another tap action. |
| Tap Insert | Allow attention only from that agent for ten minutes. This immediately clears all individual mutes. |
| Tap Insert again | End “only this agent,” leaving every agent unmuted. |

“Only this agent” stays attached to its original agent when the preview moves.
It also suppresses agents registered during its lifetime. Its expiry or the
chosen agent's release ends the mode and leaves all remaining agents unmuted.
Delete has no binding or highlight while this mode overrides individual mutes.
When available, a key is red if its next tap mutes, or yellow-green if its next
tap restores attention. Without an eligible target, no new mute action is bound.
The individual/global timers continue after navigation disarms or Agent Mode
exits. Re-arm navigation to change them; ordinary Insert/Delete input is preserved
outside that layer. A backend restart clears these temporary attention policies.

Muted occupied F-keys retain 50% of their otherwise rendered brightness and 50%
of their saturation by default, including a previewed slot. This is relative to
the current frame; it does not change surrounding keys or the keyboard brightness
limit. The stored identity color remains intact and returns when the mute ends.

Muted agents may keep working, report status and ask native questions. Their
automatic keyboard calls, action controls and inactive pending-attention cue are
suppressed. The user's F-key/Enter selection can still open them and pick up a
pending question without removing the timed policy. Notification and question
lifetimes continue normally; an expired request is not revived by unmuting.
Still-live queued calls can resume when eligible. Scroll Lock still dismisses
one incoming call, independently of these timed agent policies. Ending a timed
policy does not replay a call already dismissed through Scroll Lock or pickup.

Delete hold detection uses existing firmware DOWN/UP events on the shared device
worker. Stale targets, changed policies, mode changes and long input-service stalls
cancel the pending gesture. No firmware modification or synthetic keypress is used.

### Hold an empty gap to close it

Whenever Agent Mode is active, hold any empty F-key for about
1.2 seconds. All keys in that contiguous visible gap brighten together, flash
once, and the later agents compact forward in their existing order, including
from later pages. For an F2–F5 gap, holding any of those four keys animates all
four. Positions before the chosen gap stay unchanged; later holes close as agents
pack forward. A wholly empty page is one twelve-key gap. A trailing gap with no
later agent has nothing to close.

Empty F-keys are reserved throughout active Agent Mode: short taps do nothing,
and early release cancels the entire group's feedback. In inactive typing mode
they retain ordinary input. Page/layout changes, leaving Agent Mode, interrupted input or
stale releases cancel the gesture. The original hold's release is consumed even
when an agent has moved onto that key. No task is opened by closing a gap.

Only backend display positions move. Stable IDs, ownership tokens, identity
colors, questions, notification targets and timed mutes remain with their agents.
The worker saves recoverable mappings before showing the confirmation flash;
storage failure restores the old layout and shows no success flash. Compaction
waits while startup recovery reservations are unresolved. Existing host-only
registration persistence limits still apply.

Settings are `gap_close_hold_seconds` (1.2) and `gap_close_flash_seconds` (0.12).
Status `gap_hold` identifies the complete gap, its keys, progress and phase.
These are host-owned gestures, not new agent-callable mutation tools.

- Double-tap the profile-defined mode position to enter/exit. Inactive input and
  knob behavior stay normal. Call bindings are effective only in active Agent Mode.
- On a profile with a knob, each active knob detent moves one 12-slot page: clockwise forward,
  counterclockwise backward, without wrapping. Agent slots do not move when
  another agent releases. Freed IDs can be reused with new tokens; placement
  follows the selected sorting policy.
- In overview, press the knob centre to alternate page browsing and agent
  selection on the current page. Agent selection starts at the first occupied
  slot, skips holes and stops at the page ends. Rotation only previews; press
  highlighted Enter to select and open that task, or press its F-key directly.
  Escape returns from task focus to overview/page browsing. The normal timed
  white-background return does not itself leave task focus. After focus, knob
  press can enter agent selection again; rotation follows the current function.
- Arming keyboard navigation sets the knob to single-agent browsing. A knob
  press toggles agent/page browsing, including while an agent is focused or a
  picked-up question remains pending. Selection does not reset this knob function.
  Keyboard navigation keys keep their own actions in either knob function.
  Its idle lifetime is independent of the keyboard-navigation timer, as described above.
- Selection overview uses the existing demo's adjustable trial appearance:
  dim lavender on empty F-keys, a yellow-green-dominant candidate, and yellow-green
  Enter. The main background stays unchanged. Enter uses the existing frame
  maximum so its highlight does not dim unrelated keys through normalization.
  Notification and Mute/Pick Up controls remain available alongside it.
  Pickup removes knob-only overview Enter capture. Explicitly armed keyboard
  navigation keeps Enter until its hold/timeout disarm, as described above.
- Tap an occupied F-key to select its agent, open its registered desktop task,
  and show its static identity color. This also works after pickup or mute.
  For a pending call/question, this also performs pickup and releases that
  call's controls, just like Print Screen. A pending muted question can be opened
  through its F-key without restarting the notification. Empty positions remain ordinary keys
  in inactive typing mode; active Agent Mode reserves them for gap holds.
  Paging alone does not open a task. Rapid selections replace any pending open
  request with the latest selected destination.
- With at least one logical slot registered across all pages, empty F-key positions
  use 50% of the current normal background value. This is a relative multiplier:
  changing the normal background changes the F-row background proportionally.
  Occupied identity hues are unaffected. The last allocation's release restores
  the normal background across the F-row.
- Occupied-slot colors are encoded relative to the existing global frame
  maximum from the non-F-row scene, including active notification/control lights.
  For example, a steady background at value 128 gives a full-value slot an
  encoded value of 128, not 255. Slot acquisition/release therefore does not
  raise/lower the frame maximum and dim/brighten unchanged non-slot keys.
  Empty-slot backgrounds keep their separate relative multiplier. With a black
  base, occupied slots and mode signals remain visible. This uses the existing
  scene data and per-key transport; no extra reference key is lit or bound.
- Escape leaves the selected agent's focus, immediately restores the routine
  background, and keeps Agent Mode active. A pending question remains available
  through its F-key task selection. Escape is captured only in agent focus; set
  `escape_exits_focus: false` in the host settings JSON to disable this binding.
  Escape initially has a red cue while this binding is active. That cue fades
  to the normal background along with the selected main-area color; the binding
  remains available after the lighting returns. Yellow-green
  remains the positive confirm/answer cue; the existing strong call colors stay
  pure green for pickup and red for mute.
- Solid red Scroll Lock mutes; green Print Screen picks up the current call. Later calls
  queue; mute parks a call and permits the next unhandled call. Either mute or
  pickup releases both call bindings and restores mode lighting. The mode key
  retains its separate hold/double-tap gesture role. A handled call stays dismissed across retries, slot selection,
  focus exit and notification expiry. Only a new incoming call can expose the
  controls again. Muted tasks remain accessible through their F-keys.
- An incoming notification presents itself without prior F-key selection. Its
  attention target is separate from focused-agent selection. A new request
  bypasses an earlier browsing hold; an existing current call or picked-up
  question still retains its normal queue priority. Muting the only call leaves
  neither Print Screen nor Scroll Lock assigned to that handled call.
- In active mode, F-key selection and pending-call controls coexist. Browsing an
  agent without a pending call does not remove another call's Mute/Pick Up
  controls. Selecting a different pending call picks it up directly, parks the
  previous ringing call in its queue, and releases the picked call's controls.
  Inactive typing still requires activation first.
- Pickup opens the caller's task and switches to its page. Legacy agent-reported
  questions can show experimental answer-key hints; automatically observed
  questions show a call without guessing shortcut keys. A picked-up question keeps the
  main display until resolved or explicitly selecting another agent.
- When an unhandled notification's attention timer expires, its unanswered
  question remains pickup-able until its own expiry. Its quiet pickup action remains
  available without restarting the notification or showing an unavailable mute.
  An already muted or picked-up call never regains controls through expiry.
- Outside the selection/navigation bindings described above, native answer keys
  remain ordinary input. Abralia does not synthesize answers.
  The old numbered-key hints are experimental and have not worked with the
  inspected desktop asynchronous question card. Use the native UI to answer;
  task pickup does not guarantee panel focus or a working number shortcut.
- An asynchronous question card
  can disappear at turn completion, skip, or expiry; an agent must clear its
  reported question when that lifecycle change is known. The observer below
  tracks tool/reply records, not every native card/focus transition.
- Question cleanup restores the pre-question slot state unless the agent has
  already reported a newer state, clears its main-region focus and hints, and
  removes focus-only bindings. The backend's question timer performs the same
  cleanup on expiry; it never records that timeout as an answer.

The default requested background is static white at 50% frame value. The host can set it
anywhere from 0–100%; zero makes only the background black, while agent status,
notification and answer-key signals remain visible. Change it while running:

```sh
abralia-backend set-background --project /path/to/project --percent 25
```

This is a host command, not an agent-callable MCP tool. Runtime changes last
until restart; set `background_brightness_percent` in the settings JSON for a
different startup value. Notification preparation and timed background returns
use the chosen white level.

The backend preserves the keyboard's own global brightness setting as its upper
bound. Playback, refresh, effect handoff and cleanup do not write or restore that
brightness register, so keyboard-side adjustments remain authoritative. The
adapter's explicit `BrightnessPolicy.PRESERVE_KEYBOARD` enables this behavior;
standalone RGB API callers retain the older host-controlled policy by default.

The current firmware normalizes frame values to the brightest key before applying
the keyboard's limit. Consequently, requested 25%/50% background values are
relative frame values, not guaranteed absolute percentages of that limit. A
uniform dim-white frame can be normalized up, and a varying frame peak can change
background intensity. Encoding routine slots against the existing frame maximum
prevents registration from causing that change; it does not make uniform white
an absolute 25%/50% of the hardware limit. The backend preserves the keyboard
limit and uses the existing firmware protocol.

Routine slot colors are steady and inactive colors retain 75% saturation;
notification animation is exempt. New notifications add a brief V-only breath
to the notifying task's visible F-key, using its existing slot color and value
ceiling. By default it makes three smooth breaths over six seconds, down to 65%
of that key's normal V. The timer starts at notification arrival, even for queued
calls; off-page aging and retries do not restart it. Mute, pickup, withdrawal and
expiry stop the cue. Existing selection-preview and keyboard-guide visuals take
precedence. Other keys and the keyboard's brightness limit are unaffected.
Pause keeps its independent mode cue: inactive
Pause varies saturation between white and yellow-green when attention is available;
active Pause keeps this mode cue even while a call is ringing, changing HSV
saturation at fixed V. The separate red Scroll Lock mute cue remains solid.
This saturation breath does not change the global frame maximum. Static status backgrounds return to white
after a 15-second hold and a smooth five-second fade, without deactivating input
or expiring a pending question. The red Escape cue follows the same fade to the
background color. Every direct selection and pickup starts this timer, including
selection without a notification; selecting again restarts the hold. Pressing
Escape still exits focus immediately. Question hints are visible while active
after pickup and have their own lifetime.

## Host configuration and inspection

`serve` enables the experimental Codex observer by default. Use
`--no-observe-codex` to disable it. A background reader watches only Codex tasks
registered in the backend, through MCP or host registration. It validates each session header against
the native task ID and selected project; it does not register unrelated tasks.
The reader submits metadata to the same serialized broker worker as MCP and
never accesses HID itself.

The observer reads the existing Abralia metadata hook journal and the registered
task's local Codex session log. The journal path is discovered from the managed
project probe definition, or supplied with `--codex-hook-journal`. Session logs
default to `$CODEX_HOME/sessions` (otherwise `~/.codex/sessions`); override that
home with `--codex-home`. Hook installation and trust remain separate actions.
Starting the backend does not install hooks or grant trust, and the trusted
probe continues to write metadata only.

`get_status` returns `allocation.observed` separately from the agent's `state`:
`execution`, `turn_id`, question stages, the latest hook metadata and source
errors. Turn completion means idle, not completion of the user's project.
Ordinary state observations remain quiet and preserve task identity colors.
A fresh native `task_complete` record creates one turn-finished call for an
already registered task. This is independent of agent-reported state and uses
the normal queue, mute, pickup and fog lifecycle. Repeated observations do not
restart a handled call. Startup and replaced/truncated logs establish a baseline
without announcing historical completions; turns entirely between later polls
are still detected. An interrupted turn, unavailable log, or `Stop` hook alone
does not create a completion call: Codex can continue after a stop hook. This
automatic behavior requires the observer and readable local Codex session logs;
explicit agent attention requests remain available separately.

`allocation.observed.completed_turn_ids` lists recently detected fresh native
completions. A completion call has `origin: codex_turn` and its native turn ID as
`request_id`; `codex_turn_attention` records creation and `call_presented`
records promotion into the call queue's active position. These distinguish
automatic completion from an agent's explicit notification request. A turn that
finishes before the initial log baseline, or during a read-error recovery gap,
can be missed. A full queue logs `codex_turn_attention_skipped` without retrying
that completion later.

An accepted asynchronous question automatically creates a Codex-origin call.
For blocking `request_user_input`, an invocation in a recorded Plan-mode turn
creates a provisional call; rejection clears it. Pickup, mute, queueing and
page switching use the existing controls. Matching replies close the automatic
call; turn end closes it as `turn_ended_unconfirmed`, never as an answer.
The host's `question_seconds` also bounds local attention/focus when native
skip or expiry is not recorded. That timeout is not proof of native expiry.
The shorter notification timer does not expire the pending question.

Native request IDs and allocation generations scope automatic calls. Repeated
records cannot restart an onset or override mute. Agent-requested notifications
remain independent: withdrawing one does not clear the other. A picked-up
native question holds its display while later calls queue. An existing legacy
`report_question` suppresses a duplicate automatic onset; its agent still owns
the explicit `clear_question` lifecycle.

This adapter depends on the installed Codex log format. Missing/invalid logs
produce an unknown observation; they do not prove a task finished. Metadata is
retained in the broker, while prompt, answer and command text are discarded.
Native approval execution, automatic question-panel focus and answer-key
mapping are outside this adapter. Hardware behavior needs separate acceptance
after restarting the backend with the new code.

For read-only inspection of one known Codex session, use:

```sh
python -m abralia.backend.codex_rollout \
  --project /path/to/project --thread-id <native-thread-uuid> \
  --rollout /path/to/rollout.jsonl
```

This validates the session/project association and returns turn state and
question-call/reply metadata. It needs no hooks, does not open HID, and omits
question/answer/command text. The local log format is version-dependent; the
result distinguishes tool acceptance from an observed reply and does not infer
native skip/expiry or project completion. Continuous broker ingestion is not
provided by this inspection command.

`serve --settings /path/to/settings.json` accepts overrides from `BrokerConfig`:
background brightness 50%, notification lifetime 300 seconds, question lifetime 600 seconds, disconnected
owner grace 30 seconds, background delay 15 seconds, fade 5 seconds, inactive
saturation 75%, and 30 FPS. These timers have different meanings. The bridge
pings every 5 seconds; the service considers a connection lost after 20 seconds
without traffic. A live but quiet caller is not treated as completed.

Overview trial appearance settings are `overview_tint` (default `DDD2FF`),
`overview_tint_percent` (16), and `overview_highlight_percent` (85). They are host
settings, not model arguments. Status includes `knob_mode`, `overview_available`,
`candidate_slot`, and `candidate_key`. `knob_rotated` diagnostics record active
detents even at a clamped boundary, where `changed` is false.
Selection/page settings are `selection_brightness_percent` (130),
`selection_saturation_percent` (85), `page_display_seconds` (5),
`knob_selection_idle_seconds` (60), `page_occupied_color` (`00BFFF`),
`page_current_color` (`FF9000`) and `page_occupied_brightness_percent` (35).
Status also includes `knob_selection_remaining_seconds` and `page_display`
with visibility, bank offset, page/key assignments and capture availability.
Selected-slot V breathing uses `selection_breath_min_percent` (85) and
`selection_breath_seconds` (2); setting the minimum to 100 makes the cue steady.
Notification-slot breathing uses `notification_slot_breath_seconds` (6; 0 disables
it) and `notification_slot_breath_min_percent` (65; 100 keeps V steady). These are
host appearance settings, not model arguments.

Keyboard navigation settings are `keyboard_navigation_enabled` (true),
`navigation_hold_seconds` (0.8) and `navigation_timeout_seconds` (15). Status also
includes `navigation_active` and `navigation_remaining_seconds`. Terminal status
adds `navigation_input` with source, whether a physical mode-key press has been
observed, and any readback error. Readback failure disarms navigation and removes
its hold mapping while the normal device recovery path handles connection loss.
No encoder is required: the device layer omits unsupported encoder/knob bindings.
Timed attention settings are `agent_mute_seconds` (600),
`restore_all_hold_seconds` (1.2), `muted_brightness_percent` (50), and
`muted_saturation_percent` (50). They are host settings, not model arguments.
Each allocation's `attention_policy` reports its effective mute source and time
remaining. Overview status includes `only_agent_slot` and
`only_agent_remaining_seconds`. These attention policies do not grant agents
permission to synthesize user input.
`navigation_colors` is a host setting with all three keys:
`{"page":"00BFFF","slot":"FF9000","boundary":"A060FF"}`. It changes assigned
navigation keys only, independently of `overview_tint` and the strong call colors.

To exercise navigation without hardware, start a separate simulated backend and
use `simulate ... navigation_hold`, `last_agent`, `first_agent`, `page_next`,
`page_previous`, `slot_next`, `slot_previous`, or `confirm`. These terminal-only
events are rejected in hardware mode.

The host returns the active `identity_color` with each slot. Both cached palettes
stay stable across status changes and paging; sorting selects the individual or
project palette. Live-client recovery preserves both colors across backend
restarts. Explicit notification requests drive the existing expand/fill/breathe
effect, which blends toward the task's color, then condenses into a drifting fog
orb if it remains unacknowledged. A status update alone stays quiet.
The older `colors` setting remains for native answer hints and the isolated
page-marker demo; it no longer determines production task identity colors.
The per-owner notification cooldown defaults to 5 seconds;
there are at most 256 queued/pending calls and 4096 remembered idempotency results.
Logical slot allocation is not restricted to twelve.

### Notification fog

Arrival animations run sequentially, independently of the pending-call queue.
After the yellow-green onset, the host spends eight seconds transitioning and
breathing in the task color, then two seconds forming an orb. Each task has one
orb covering its outstanding notifications. Distinct new notifications refresh
it after presentation; retries do not duplicate it or restart its age.

Orbs drift across the typing area, navigation cluster and arrows. Smaller
collision cores deflect one another while the fog envelopes overlap. Colors mix
in linear RGB within the existing frame brightness reference. F-slot, mode,
action, page and question cues retain precedence. Moving orbs do not change
bindings or the keyboard's global brightness setting.

Pickup and direct F-key/Enter selection acknowledge the task's existing visual
reminders. New arrivals after that action remain eligible. Call mute leaves a
quiet orb; timed agent mutes hide the affected orbs while their age advances.
Held questions defer foreground animations. Viewing a task directly in another
application is not automatically detected; URL dispatch is not focus proof.

Default orbs hold for 120 seconds and fade for 60 seconds. Visual expiry never
answers a question or marks it viewed. Cancellation, native resolution and slot
release remove the corresponding coverage. Orbs are not restored after backend
restart. Host-only settings are `notification_breath_seconds` (8),
`orb_formation_seconds` (2), `orb_hold_seconds` (120), `orb_fade_seconds` (60),
and `orb_dismiss_seconds` (0.5). The notification's existing lifetime remains an
upper limit, independently of pending-question lifetime.

### Optional custom notification animation

Customization is optional. Agents should use the default unless a useful effect
comes readily to mind; ordinary work should not spend extra reasoning, research
or iterations on decoration. `set_notification` omission uses the saved slot
default, while `animation="default"` explicitly selects normal breathing.
`set_notification_animation(animation=null)` clears a saved default. Defaults
belong to the allocation and are not restored after a backend restart.

A custom description contains `duration_ms` (1000–4000) and up to four layers:

```json
{"duration_ms":3000,"layers":[{"shape":"ring","center":[0.5,0.5],"radius":[0.05,0.6],"color":"slot_highlight","opacity":[0.7,0]}]}
```

Shapes are `ring` (`center`, `radius`, `width`), `spot` (`from`, `to`, `radius`),
`sweep` (`axis`, scalar `from`/`to`, `width`), and `pulse` (`center`, `radius`,
`pulses`). Coordinates use 0–1 within the main typing region. Radius may be a
constant or start/end pair in .02–1; width is .02–.25. Layers may set `start_ms`
and `end_ms` within the clip, spanning at least 300 ms. Opacity is a constant or
start/end pair in 0–0.7; pulses are limited to 1–3. Endpoints are eased by the
renderer, and combined opacity never exceeds 0.7.

The approved animation palette is `slot`, `slot_highlight`, `slot_shadow`,
`positive` (yellow-green) and `negative` (red). The skill describes their meaning;
the backend enforces the palette, shape and resource limits. No executable code,
files, arbitrary RGB, physical LED indices or input operations are accepted.

The normal four-second transition remains, followed by the custom clip over a
steady slot-colored background. A short clip leaves that background steady for
the rest of the existing breathing window, then the usual fog formation follows.
No queue time is added. The default eight-second window allows at most four
custom seconds; a shorter host window reduces that allowance. Controls remain
visible above the overlay and the keyboard brightness ceiling stays authoritative.
Pickup/mute immediately ends the presentation. Each new notice snapshots its
animation; retries or later default changes cannot alter an existing call.

`agent_animations_enabled=false` disables custom clips through host settings.
`get_status.animation_capabilities` reports limits. Unsupported descriptions fall
back to normal breathing with feedback in `notification.animation`; an invalid
outer MCP request can still be rejected normally. Fallback never requires a
model repair loop and must not cause a duplicate notification.

### Static keyboard guides

`show_keyboard_frame` takes a `colors` mapping from physical key IDs (for example,
`W`, `A`, `S`, `D`, `SPACE`) to the palette above, `white`, `off`, or six-digit RGB.
Use `get_status.keyboard_frame_capabilities.keys` for the selected profile's
renderable keys. Unspecified keys use the idle white background. Escape is
reserved red; the physical mode key retains its normal cue.

The tool creates a separate, owned notification. It never takes over lighting
until the user picks up that call in Agent Mode. Direct F-key selection can also
open the guide, including after mute or attention expiry. During the guide,
only Escape is captured by the backend; F-keys, arrows, Enter and the knob revert
to ordinary input, so the guide does not remap the highlighted keys. Firmware's
double-tap mode gesture remains available. Inactive mode hides the guide until
Agent Mode resumes. Escape dismisses the guide and restores normal Abralia
lighting/bindings; other queued calls can then continue.

A guide has no independent fade timer. It ends on Escape, exact-ID withdrawal,
replacement, slot release or normal disconnected-owner cleanup. It never answers
a native question. A replacement needs a new pickup, and old Escape releases
cannot dismiss it. Repeated identical guides retain their ID and do not re-notify.
The normal notification cooldown limits distinct replacements. A saved animation
default applies to the guide's arrival; do not request a second call separately.

Generate offline examples of the four clip primitives and the guide's pickup/
Escape sequence with Pillow installed in the chosen development environment:

```sh
python experiments/desktop-rgb-physical-validation/custom_visuals_preview.py --output /path/to/previews
```

From the repository root, a bounded fixture trial uses the production renderer:

```sh
python experiments/desktop-rgb-physical-validation/notification_fog_demo.py --fast
```

It simulates three tasks and uses shortened hold/fade timings. For a physical
trial, stop the backend and replace `--fast` with `--mode hardware`; Ctrl-C or
the 75-second deadline requests RGB restoration. These fixtures do not open
conversations. Generate color-mixing images and a keyboard animation without
hardware using Pillow in the chosen Python environment:

```sh
python experiments/desktop-rgb-physical-validation/notification_fog_preview.py --output /path/to/previews
```

```sh
abralia-backend status --project /path/to/project --compact
abralia-backend fixtures --project /path/to/project --count 25
abralia-backend simulate --project /path/to/project toggle
abralia-backend simulate --project /path/to/project next_page
abralia-backend clear-fixtures --project /path/to/project
```

Fixtures are explicitly labeled simulated agents and are terminal-only test
operations. Simulated key events are rejected by a hardware-mode backend.
`status` is an OS-user diagnostic view; the model's `get_status` does not expose
other agents' private summaries. Optional `serve --log` records events and IDs,
not slot tokens or full question text.

Run the desktop tests with:

```sh
python -B -m unittest discover -s abralia/desktop/tests
```

The backend tests use fake time, a simulated device, private temporary project
directories, and real STDIO MCP clients. They need local Unix-socket permission.
They do not access the keyboard. MCP SDK 2.2.0 is an optional MIT-licensed
dependency from the [official Python SDK](https://github.com/modelcontextprotocol/python-sdk);
its dependencies retain their own licenses. No SDK source is copied here.
