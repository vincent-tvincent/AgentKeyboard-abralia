---
name: abralia
description: Use this project's Abralia keyboard after the user explicitly asks to enable it for this task; report progress and attention through the available Abralia tools.
---

# Abralia keyboard

Activate only after the user explicitly asks to use Abralia for this task.
Plugin/tool availability, project enablement and ordinary coding requests are
not activation requests. Once authorized, keep the allocation through follow-up
questions. If the user disables the project or releases your slot, do not
re-enable it without a new explicit request. The installed plugin's `enable_self`
can enroll the verified current project and acquire a slot; this legacy
project-local setup uses `acquire_slot` in an already configured project.

Use the `abralia_experiment` MCP tools for this project's keyboard. The backend
owns appearance, paging, input mode and cleanup. The user physically picks up
calls; an agent cannot synthesize pickup, mute, or an answer.

**Default notification effects are enough.** If you do not already have a clear,
useful animation idea, omit customization and continue your main task. Do not
spend extra reasoning, research, tool calls or iterations designing a decorative
effect. Custom animation is optional, not part of completing ordinary work.

## Own one slot

- After the explicit activation request, call `acquire_slot` with a concise task label, unique
  idempotency key, and your actual `harness`: `codex_desktop` when running in the
  desktop app, `codex_cli` in the terminal client, or `unknown` if unclear.
  This registers you and allocates your slot in one call. Desktop registration
  enables conversation pickup and question hints without a manual host command.
  MCP supplies your own task identity; never invent or ask the user to register
  a task ID. Your harness report is recorded as `agent_reported`, not verified
  UI focus. Retain the returned `slot_token` for subsequent calls.
- The user may have registered your task through the host without waking you.
  If `get_status` shows `registration_source=host` and `agent_attached=false`,
  call `acquire_slot` normally to adopt that existing slot. It keeps its position
  and identity color; the ownership token is returned only after acquisition.
  Host registration does not prove that your model was running. Admin release
  can invalidate your allocation. If the user released it, wait for a new explicit
  activation request. Ordinary MCP tools never register or release another task.
- If an existing allocation has an unknown harness, repeat `acquire_slot` with
  the known harness and a new idempotency key; it keeps the same slot/token.
  A different known harness requires release and reacquisition. After cleanup,
  acquire again with your harness. A backend restart may recover the slot through
  the surviving bridge, but always invalidates the previous token; see recovery.
- Your logical slot stays yours even when its F-key page is not visible. Do not
  acquire another slot just because the user changed pages.
- Treat `slot_id` as an allocation identity, never as a physical position. The
  user can close gaps and move your displayed key without changing your ID or
  token. Use `get_status` to look up `display_position`, `page`, `f_key` and
  `layout_revision` before giving a key-location instruction. Never calculate
  F-key/page from the ID or release/reacquire merely because it moved.
- Report `progressing`, `error`, `action_requested`, `completed`, or `idle` with
  `set_slot_state`. Numerical progress is optional; use it only with a real basis.
- Call `set_notification(enabled=true)` when a meaningful result, concern, or
  review request warrants attention. Status changes alone are quiet. User mute
  applies to that call and cannot be overridden by repeating it.
- In active Agent Mode, green Print Screen picks up and solid red Scroll Lock
  mutes. Pause retains double-tap activation and hold-to-navigate; it never mutes.
  Mute and pickup both release Print Screen/Scroll Lock call bindings. Selecting a
  handled task's F-key opens the task without bringing those controls back.
  Only a new incoming call can expose them again. Do not re-notify merely to
  undo a user's mute; ordinary retries refer to the same call.
- Selecting a pending call's F-key, or confirming it with overview Enter, also
  counts as pickup. This releases its call controls and preserves a pending
  question until its native reply; pickup is not an answer.
- Call `release_slot` when its display and calls are no longer needed. Releasing
  also withdraws pending calls, so do not immediately release an unread completion
  call you intend the user to pick up.

## Terminal and editor pickup

Report `codex_cli` when you run in a terminal, including an editor's integrated
terminal. In a VS Code extension without a terminal, report `unknown`; native
editor process context may still support window pickup. The bridge collects its
own native process and terminal context; never
invent or supply a terminal ID, PID, socket path, window title, command, or URL.
A supported adapter may focus an exact terminal or only its owning window.
Window focus does not prove a particular pane, conversation, or question is
visible. Inspect the returned pickup result and report that distinction honestly.
Missing or stale routing metadata leaves status and notifications available;
never repair it by sending keystrokes or opening a guessed task location.

## Optional notification animation

Only use this when an idea comes readily to mind, or the user specifically asks
for animation design. Otherwise keep the default; do not invent an effect just
because the tools support it. If an animation falls back or the capability is
unavailable, accept the default and continue instead of entering a repair loop.

For a single call, `set_notification` accepts optional `animation`. Omission uses
the slot's saved default (ordinary breathing unless you deliberately changed it);
`animation="default"` explicitly uses ordinary breathing for this call.
`set_notification_animation(animation=...)` saves a default for future calls,
including automatically observed questions, without triggering a notification.
Passing `null` to that setter restores ordinary breathing. Existing calls are
immutable: changing the default or retrying does not replay or unmute them.

Descriptions contain `duration_ms` (1000–4000, within host limits) and 1–4 layers.
Each layer has `shape`, optional `color`, `opacity` (0–0.7 or a start/end pair),
and optional `start_ms`/`end_ms` inside the clip. Coordinates are normalized 0–1:
`ring` uses `center`, `radius` (value or pair), `width`; `spot` uses `from`, `to`,
`radius`; `sweep` uses `axis` (`x`/`y`), scalar `from`/`to`, `width`; `pulse` uses
`center`, `radius`, `pulses` (1–3). Radius is .02–1, width .02–.25, and each layer
lasts at least 300 ms. Backend easing, opacity limits and reserved keys are fixed.

Use `slot`, `slot_highlight` or `slot_shadow` to retain task identity. `positive`
is yellow-green for confirmation/readiness; `negative` is red for a concern or
cancel-style message. Pick a semantic accent only when its meaning fits. Do not
invent RGB values, imply a user approved anything, or imitate pickup/mute keys.
The background stays the slot color and the existing controls remain visible.

## Static keymap guides

When a visual keymap would help the user, inspect
`get_status.keyboard_frame_capabilities.keys` for valid physical key IDs, then
call `show_keyboard_frame(colors={...}, summary=...)`. Colors can use the palette
above, `white`, `off`, or six-digit RGB for a clearly explained keymap legend.
This creates its own call; do not send a separate notification for the guide.
It appears only after pickup in active Agent Mode. While visible, highlighted
keys keep ordinary input behavior; Escape is reserved red and dismisses the
guide. The physical mode key keeps its normal cue and double-tap gesture. The guide stays
until Escape, replacement, withdrawal or allocation cleanup; it does not use the
fog/background fade timer. Use `clear_keyboard_frame` with the returned frame ID
when your guide is no longer relevant. Do not describe it as a key remapping or
a native question/approval answer. Missing support should not block the main task.

## Ask one native question

Keyboard navigation is the primary slot-navigation method; a knob is optional.
The user holds the fixed physical mode key in active Agent Mode to arm/disarm it.
Home/End span all agents, Page Up/Down and Up/Down change pages, Left/Right preview
slots, and Enter confirms. `get_status` exposes `navigation_active`. Selecting
an agent leaves this mode armed until a 15-second navigation idle timeout or
another hold. If it is armed, arrows and Enter still navigate agents. After the
navigation-key timeout, the separate knob selector may retain Enter and number
keys; confirm the agent or return the knob to page mode before using them in a
native question. The selector expires after 60 seconds without selection input.
Never synthesize input or force a mode change to answer a question.
Arming navigation starts the optional knob in single-agent browsing. Knob press
toggles agent/page browsing even after task selection; keyboard actions stay fixed.
The number row briefly shows pages after a page change, with the current page
orange. While knob agent selection is active, occupied page numbers are captured
for direct page jumps; they are not native question-answer shortcuts. Status
`page_display` gives the exact mapping and whether capture is active. A passive
page display does not capture digits. A brighter, slightly whiter candidate is
a preview; it is not proof of task pickup or an answer.
In knob agent-selection mode that candidate also breathes on brightness (HSV V).
Other task slots remain steady; this cursor effect is not a new notification.
Throughout active Agent Mode, holding an empty F-key closes its whole contiguous
visible gap after all gap keys brighten and flash together. Later agents move
forward across pages while keeping their IDs, tokens and task state. Short taps
on these empty positions do nothing while active; inactive typing retains ordinary
input. This is a user gesture, not an operation the agent should synthesize.
Page keys use cyan, single-slot arrows orange, and Home/End violet by default.
Enter stays yellow-green; strong pickup green and mute red are unchanged.

In navigation mode, Delete toggles a ten-minute agent mute and holding it clears
individual mutes. Insert allows attention only from the target agent; pressing
it again or waiting ten minutes restores everyone, discarding earlier individual
mutes. The previewed agent takes precedence over focused agent, then incoming
caller. Delete is unavailable while “only this agent” is active. Red means the
next tap mutes; yellow-green means restore. These are user-owned controls.
Inspect `allocation.attention_policy` for your effective mute and remaining time.
Keep working and ask native questions normally while muted; do not retry
notifications to defeat it. Direct user pickup remains possible. Scroll Lock's
single-call dismissal and native question expiry retain their separate meanings.

First inspect `get_status`. When `allocation.observed` has source
`codex_observer`, a known execution state and no source error, ask the native
question normally. The backend observes acceptance/replies and creates its own
call. Do not also call `report_question` or request a second notification for
that same question. Automatic pickup opens the task but does not focus the
question panel or provide a verified shortcut mapping. Use native UI controls
to answer. Agent-requested attention for other reasons remains available.

When automatic observation is unavailable, use the explicit fallback below.
Its numbered key hints are experimental: the inspected Codex desktop async
question card did not accept number-key selection. Never promise that these
highlights will answer a question or substitute them for the native question UI.

1. Before the native question call, call `report_question` with the slot token,
   a new question ID, `kind` (`single_choice` or `free_text`), and the exact ordered
   options as `{id, label}` objects. Set `allow_other=true` when the native UI
   offers a free-text alternative. For a plain free-text question, use no options.
2. Immediately ask that same question using the available native Codex user-input
   tool. Abralia only prepares a notification and highlights; it does not display
   the question or wait for the answer itself. Ask one question at a time so
   native page changes cannot make the displayed mapping stale.
3. The user double-taps the physical mode key to activate, then taps green Print
   Screen to pick up. Pickup opens this task, selects its slot page, and reveals
   the question's experimental key hints. These keys remain ordinary input to
   Codex; actual navigation depends on the native question panel and its focus.
4. After the native answer, call `clear_question` with the same question ID and
   `outcome=answered`. Use `cancelled` or `withdrawn` when appropriate, then update
   your slot status. Pickup by itself is not an answer or native approval.

For `request_user_input_async`, the desktop app can remove the live question panel
when the turn finishes, when the card is skipped, or when its timer expires.
Questions may expire or be skipped: do not prolong the turn just to prevent that.
While genuinely waiting for an answer, continue useful work or use bounded,
interruptible waits. Before finishing with no answer, withdraw the Abralia
question. On a known native skip or expiry, clear it with that outcome. If the
native tool supplies no lifecycle/focus signal, do not claim automatic detection
or that highlighted keys answered the question. Tool acceptance and task opening
alone do not prove question focus.

Escape leaves Abralia focus and restores the background. It does not answer or
cancel your native question. Keep a still-pending question available for later
F-key task selection until its native answer, explicit withdrawal, or expiry.
Escape initially has a red quit cue while it is bound. The selected main-area
color and Escape cue share a timed fade to the background; the fade does not
end task focus or answer/expire a question. Escape remains functional afterward.
Yellow-green is the positive
confirm/answer cue. Strong call colors remain green pickup and red mute.

Conversational yes/no questions use two ordered options. Native permission
approval and multi-select integration are not supported by this first version.
Do not represent Escape as a universal reject shortcut.

## Results and recovery

`accepted` means the backend admitted the report. Inspect `delivery` separately:
simulated output or a HID write does not prove the user saw it. `get_status`
returns readiness and only your own allocation and recent physical feedback.
Reuse the same idempotency key only for a retry of identical arguments. New
updates need new keys. After backend restart, a surviving MCP bridge can restore
your position and identity color if the original client process is still alive.
The old token remains invalid: call `get_status` without it to obtain the current
allocation, or acquire once with a new idempotency key. Reacquire only if your
display is still needed. Do not recreate an absent allocation just to release it;
the bridge excludes an uncertain release from automatic recovery. Restarting the
bridge itself requires registration again. Its heartbeat handles reconnection;
do not add model polling or attempt to reload the user's client yourself.

Automatic observations are separate from your reported slot state. A turn end
is not project completion; local attention expiry is not proof of a native
answer or expiry. Recovery restores stable allocation metadata, not old calls or
question contents. Observation resumes after restoration or acquisition and may
rediscover a still-pending native question. Hook installation/trust is handled
by the user, not by this skill.

If tools are missing or return `skipped` because the backend/device is unavailable,
continue the user's main task and ask questions normally. Do not install, repair,
poll continuously, or add periodic model heartbeat calls merely for lighting.
The bridge maintains liveness while a long native question or other tool runs.
