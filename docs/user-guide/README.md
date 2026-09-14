# Using Abralia

Abralia gives each registered task a colored F-key slot. It can call for your
attention, let you pick a task from the keyboard, and keep quiet reminders for
requests you have not handled yet. This guide describes the current default
controls and lighting; host configuration can change the documented timings.

## Start here

1. Choose your exact [keyboard model](keyboards/README.md). The **mode key is a
   physical position**: Pause on the V3 8K, the top-right lighting key on the
   original V3. Remapping its ordinary keycode does not move this gesture.
2. Open Abralia, select the keyboard, and connect the integration using the
   [control-panel setup guide](../../abralia/desktop/gui/README.md#use).
   The app manages its backend; normal app use needs no separate terminal.
   Agent controls require matching Host Interaction firmware and enabled effect 25.
3. Enable a project and ask a task to use Abralia. Installing the plugin or
   enabling a folder alone does not activate every task. The Codex integration
   also supports asking an agent **“Enable Abralia for this task.”**
4. Double-tap the physical mode key to enter **Agent Mode**. Tap an occupied
   F-key to select its task, or hold the mode key to browse with navigation keys.
5. When a call arrives, use the green pickup key or red mute key. You do not
   have to select an F-key first. Double-tap the mode key again for ordinary input.

The white background stays lit when Agent Mode is inactive. A keyboard mode
change, a task selection, and a lighting return are separate actions.

## Reference pages

| I want to… | Read |
| --- | --- |
| Find every key action, its mode, and how to leave it | [Controls](controls.md) |
| Understand colors, breathing, fog, brightness and timeouts | [Lighting and effects](effects.md) |
| Locate the controls on a particular keyboard | [Keyboard models](keyboards/README.md) |
| Try the behavior without touching a physical keyboard | [Keyboard simulator](../../tools/keyboard-simulator/README.md) |
| Configure timings, inspect tasks, or use the terminal backend | [Backend reference](../../abralia/desktop/BACKEND.md) |
| Configure agent tools and the Codex plugin | [Agent tools](../../abralia/desktop/BACKEND.md#agent-tools) · [Plugin setup](../../abralia/desktop/plugin-bundle/README.md) |
| Build or install matching firmware | [Firmware guide](../../abralia/firmware/qmk-userspace/README.md) |

## What a slot means

A slot belongs to a task. Its stable identity stays with that task when sorting
or gap closing changes its displayed page and F-key. Twelve positions fit on
each page: positions 1–12 use page 1, positions 13–24 use page 2, and so on.
Colors identify tasks; a red task color by itself does not mean an error.
Separate effects and control colors communicate attention and available actions.

Browsing highlights a **preview**. Enter or an occupied F-key **selects** the
task and requests navigation to its registered destination. That destination
may be a Codex desktop task, a supported terminal session, or a verified app
window. Availability and precision depend on the integration; opening a task
does not guarantee focus inside its question panel. See
[terminal and window support](../../abralia/desktop/BACKEND.md#cli-terminals-and-vs-code-windows).

## Questions and approvals

Question notification and pickup are useful without automated answering.
Answer in the agent application's own UI. This version does not execute native
approvals, submit answers, or reliably focus the native question panel for you.
In particular, numbered option shortcuts are not a supported Codex desktop
workflow. [Question behavior](controls.md#questions-and-native-input) explains
the experimental hints and which Abralia modes temporarily use Enter or digits.

## Reading the illustrations

The linked pages include **simulated host-output illustrations**, not footage of
physical keyboards. They explain states and transitions; they do not establish
hardware compatibility or exact physical brightness. Read the written sequence
and timing tables for a motion-free reference. Long waits may jump forward at
the labeled simulation times; animation length is not a timing specification.
The simulator lets you inspect
the same interaction without sending HID commands or opening real tasks.

The V3 8K ANSI encoder is the reference hardware. Original V3 profiles remain
experimental and physically unverified; see each model page before choosing it.

Documentation: [Apache-2.0](../../LICENSE.md).
