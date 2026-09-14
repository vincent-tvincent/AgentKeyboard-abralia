# Abralia contributor and agent instructions

## Understand the project before proposing changes

Abralia adds an opt-in agent interface to compatible RGB keyboards while
preserving their ordinary input and stock firmware features. The current host
is Python; the macOS app is Electron/React with a bundled Python backend. Codex
integration uses semantic MCP tools and native observations. The standalone RGB
and input APIs remain usable without agents. Other platforms, harnesses and
keyboards must be described according to their actual implementation/evidence.

Complete this reading at the start of a task, before design or implementation
decisions. Reuse that context for the rest of the task; revisit relevant documents
when the scope, requirements or documentation changes. Do not repeat the full
reading or directory inventory before every phase or follow-up.

1. Read [README.md](README.md), [docs/README.md](docs/README.md) and
   [LICENSE.md](LICENSE.md) in full. Understand current support and limitations.
2. Read the applicable guides below and inspect the closest existing implementation
   and its tests before designing a solution. Follow any more-specific contributor
   instructions in the directory you will change.
3. Discover directories named `docs` at every depth in the checkout, including
   component/experiment/dependency trees. Search them for relevant history; read
   matches in context. Upstream documentation describes upstream, and an experiment
   or old proposal does not establish an approved design or physical compatibility.
4. Resolve apparent conflicts using instruction priority and current explicit
   maintainer authorization. If required guidance is missing or a decision still
   cannot be resolved, stop the affected work and ask rather than guessing. Honor
   supplied approved decisions; keep unresolved choices explicit. A clean public clone must not
   depend on unpublished parent-workspace files or a contributor's private notes.

| Work area | Required reading before design or implementation |
| --- | --- |
| Firmware or a new board port | [Firmware overview](abralia/firmware/README.md), [QMK build/protocol guide](abralia/firmware/qmk-userspace/README.md), [pinned upstream](abralia/firmware/qmk-userspace/qmk-upstream.lock.json), [firmware tests](abralia/firmware/tests/README.md) |
| Device discovery, profiles, RGB or input | [Desktop API](abralia/desktop/README.md), [profile format](abralia/desktop/src/abralia/resources/profiles/README.md), [discovery CLI](abralia/desktop/DEVELOPER_CLI.md), [Host Interaction API](abralia/desktop/HOST_INTERACTION_API.md), [compatibility layouts](abralia/desktop/COMPATIBILITY_LAYOUTS.md) |
| Broker, rendering or keyboard behavior | [Backend contract](abralia/desktop/BACKEND.md), [controls](docs/user-guide/controls.md), [effects](docs/user-guide/effects.md), [model guides](docs/user-guide/keyboards/README.md), [simulator](tools/keyboard-simulator/README.md) |
| GUI appearance or window behavior | [GUI guide](abralia/desktop/gui/README.md); also read the backend contract for changes to backend controls |
| Packaging or agent integration | [GUI guide](abralia/desktop/gui/README.md), [backend contract](abralia/desktop/BACKEND.md), [plugin bundle](abralia/desktop/plugin-bundle/README.md), [agent skill](abralia/desktop/plugin-bundle/plugins/abralia/skills/abralia/SKILL.md) |

## Change discipline

- State the existing component/pattern and requirement the change extends.
  Prefer the smallest compatible change; do not redesign working behavior from
  scratch, introduce parallel implementations, or add unrelated refactors.
- Obtain maintainer agreement for architecture, protocol, shared interaction or
  license changes. An already explicit request authorizes its stated scope;
  do not repeatedly ask approval for routine implementation within that scope.
- Do not silently change public schemas, wire formats, effect IDs, defaults or
  capability meanings. Propose compatibility/versioning and recovery together
  when a contract really must change. Never change tests merely to hide a regression.
- Keep simulator/test fixtures separate from live projects, devices and settings.
  Preserve user edits and unrelated work. Do not commit generated builds, private
  logs, machine configuration, tokens or credentials.

## Firmware: additive extensions, not replacement firmware

- Start from the existing ports in [QMK userspace](abralia/firmware/qmk-userspace/).
  Read the V3 8K reference and the closest sibling target; identify the actual
  board differences before editing. Do not invent another implementation of the
  same RGB, binding or gesture state machine for each keyboard.
- Use the existing thin-wrapper/include pattern by default: retain the pinned board's
  upstream keymap and hooks, reuse Abralia's shared implementations, and change
  only necessary target configuration, layout metadata or adapter glue. A justified
  shared-module refactor or different composition mechanism is acceptable when
  it preserves shared behavior, stock compatibility and recovery, with appropriate
  tests. Architecture or contract changes still require maintainer agreement. Preserve
  each board's own MCU, bootloader, scan/LED drivers, USB identity and polling;
  copying V3 8K descriptors does not give another board 8K support.
- Preserve stock layers/Fn, ordinary keys, knob behavior where present, VIA and
  Keychron Launcher compatibility where the target provides them. On the current
  Keychron family, keep effects 0–24 and vendor commands intact; effect 25 is
  additive. Do not remove features to free resources without explicit agreement.
- Keep firmware generic: RGB transport, physical input routing, capabilities and
  recovery belong there. Agent identity, projects, notification meaning, sorting,
  navigation policy and application-specific animation belong in the host.
- Preserve volatile guarded frames, sequence/commit checks, frame timeout,
  heartbeat recovery, atomic binding generations, event acknowledgements and
  held-key release. Effect changes must disarm input; returning to effect 25
  must not reactivate it automatically. Preserve documented manual activation
  and the low-level API's separately authorized force behavior.
- Transient host behavior must not add EEPROM writes, alter stock key mappings,
  reset saved preferences or overwrite the keyboard's global brightness limit.
  Preserve existing deliberate EEPROM/version-reset behavior and document any
  explicitly approved change rather than bypassing it for a demo.
- Keep the variants distinct: `led_only_not_interactable` provides lighting;
  `abralia_host_interaction` also provides agent input routing. Do not imply the
  lighting-only variant supports interaction. Images are exact-target specific.
- If hardware cannot reuse the current transport/capabilities, explain why and
  propose a scoped extension. Do not weaken shared invariants or transplant
  unverified reference-board assumptions just to make the port compile.

## New keyboard support must remain portable

1. Establish exact product, revision, layout, interface and firmware protocol.
   Use the documented scan/probe/draft workflow; discovery is not compatibility
   proof. Missing or ambiguous metadata remains unknown, not guessed.
2. Put VID/PID and interface matching, matrix dimensions, encoder count, physical
   geometry, LED addresses/points, regions and the reserved mode position in the
   explicit [profile resources](abralia/desktop/src/abralia/resources/profiles/)
   or target configuration. Select/validate profiles explicitly. Keep protocol
   constants in the appropriate adapter, not scattered model branches in core/UI.
3. Resolve input and lighting through physical elements and Control IDs. The
   reserved toggle comes from `interaction.toggle_matrix`, not a search for the
   current Pause keycode. A VIA remap does not relocate physical controls. Knobs
   are optional; preserve the keyboard-navigation path on knobless models. LED
   addresses, key controls and encoder actions are distinct; do not assume one
   LED per control or invent interaction controls for an RGB-only profile.
4. Use package resources, configuration and OS path APIs. Do not commit real
   user/device identifiers, personal paths or machine-specific executable paths;
   synthetic test fixtures and documented protocol/OS constants are valid. Do not
   guess a user's checkout. Built-in profiles must work without a QMK source tree
   at runtime. Machine-specific launch paths belong in generated private install
   state; packaged runtime and source-tree operation must both remain valid.
5. Extend existing profile/adapter/discovery interfaces. The GUI's reviewed
   profile registry is an explicit listing boundary; do not move model/PID
   allowlists or implicit profile selection into the generic RGB/input library.
   A different wire protocol needs an adapter, not a JSON claim of compatibility.
6. Validate/export the completed profile (`--for-agent` for agent controls), add
   focused mapping/capability tests and a model guide, and exercise it in the
   simulator. Label source-only, built, simulated and physically verified support
   separately. Keep unsupported variants excluded until their evidence exists.

## Preserve the host and interaction boundaries

- One backend worker serializes hardware operations through one
  `SharedRawHidSession`. RGB and input share it. MCP bridges, GUI pages and
  observers must not open competing HID handles or block the device worker with
  filesystem scans, native window focusing or other slow external work.
- Preserve the flow: semantic/physical scenes → compatibility mapping → device
  adapter → firmware. Keep broker policy, renderer and input routes testable
  without hardware; the simulator must reuse production logic, not fork it.
- Native task/project metadata and allocation tokens establish ownership, never
  display labels or model-supplied identities. Keep retry/idempotency, stale-token,
  connection-recovery and delayed-input generation checks intact.
- Slot identity is separate from page/F-key position. Sorting, paging and gap
  closure may move a display position without changing ownership or task state.
  Persisted layout/registration updates must roll back on failure; do not publish
  new bindings before the corresponding persistence transaction succeeds.
- Reading this file, installing the plugin or enabling a project does not ask an
  agent to register. Preserve explicit user activation and authorized host
  registration; passive hooks/observers must not allocate slots or enroll projects.
- The broker decides appearance, queueing, pickup/mute, lifetimes and permission.
  Agents receive semantic operations, not raw HID, forced mode activation,
  arbitrary native keystrokes or approval execution. Extending contributor support
  to Claude Code does not by itself implement a Claude keyboard integration.
- Use the controls/effects guides as the interaction contract. Keep independent
  notification, question, selection-background and fog lifetimes; retries must
  not undo mute or replay an onset. Unbound/inactive input remains ordinary.
- Preserve identity colors and action meanings. Never fix one slot's brightness
  by dimming the whole keyboard, adding a reference F-key or raising the global
  limit. Keep routine scenes steady except for documented, bounded cues.
- The app owns backend startup/cleanup; users should not need a separate terminal
  daemon. Preserve hide-versus-Quit behavior and system theme following. Keep
  bundled runtime deployment independent of the checkout. Runtime-only updates
  must not reinstall an unchanged plugin or discard live bridge recovery state.
- Install/uninstall must preserve unrelated settings and user edits, avoid
  duplicate managed entries, and remove only Abralia-owned content. Keep hook
  trust, plugin installation, project enrollment and task activation distinct.

## Verify before claiming support

- Run checks appropriate to the affected code; do not flash or rebuild firmware
  for a documentation-only change. Reuse existing suites and add regression tests
  for changed contracts/behavior, not tests that only repeat implementation text.
- For isolated changes, run focused tests covering the affected behavior. Run
  the full applicable suites for shared logic/contracts, broad changes and release
  validation. Report platform-dependent checks you cannot run; arrange their
  verification on a supported environment or CI rather than silently skipping
  them or treating a local platform limitation as a product failure.
- The full desktop/broker suite is
  `python -B -m unittest discover -s abralia/desktop/tests` in the configured
  environment. GUI checks are `npm test` and `npm run build` in
  `abralia/desktop/gui/`. Packaging changes also need a packaged smoke check.
- MCP/installer changes: exercise the client's resolved STDIO launch against an
  isolated backend and check project isolation. Hook-specific path expansion is
  not proof that the same expression works in MCP launch arguments.
- Firmware changes: run the [offline firmware tests](abralia/firmware/tests/README.md)
  and build every provided variant of each affected target from the pinned upstream
  (both variants for the existing Keychron targets).
  Changes to shared firmware require checking all existing users of that source.
  New model/interaction work also needs the [simulator checks](tools/keyboard-simulator/README.md).
- A build, valid profile, mock test, HID write or focus dispatch is not physical
  acceptance. Report exactly what was tested and what still requires an owner
  with the exact keyboard. Preserve stock compatibility and recovery checks.
- Implementation/build permission does not authorize flashing, DFU, EEPROM saves
  or unrelated hardware changes. Obtain explicit authorization for the actual
  device/action. Start with the smallest appropriate read-only probe; if sandbox
  access fails, use approved escalation rather than declaring hardware absent or
  bypassing security. An escalated read does not authorize a write.
- Keep public documentation portable and factual. Update affected user/model/API
  guides and support status, preserve the README's short introduction and image
  wrappers, and keep private setup history/work logs out of the public repository.

## License boundaries

- Follow `LICENSE.md` and the full texts in `LICENSES/`.
- Abralia-authored firmware under `abralia/firmware/` is
  `GPL-3.0-or-later`, except where a file preserves another compatible
  upstream notice.
- Abralia-authored desktop, host, protocol, experiment, documentation, and
  root-level project files are `Apache-2.0`, unless a file states otherwise.
- Do not change these license assignments or introduce an additional license
  without explicit maintainer approval and a corresponding decision-record
  update.

## Source-file notices

- Preserve existing notices. For new files or copyrightable additions, use the
  correct copyright holder; use `blue_lobster` for work owned by that maintainer,
  not automatically for every external contribution. Attribution does not change
  the applicable SPDX license.
- New Abralia-authored firmware `.c`, `.h`, and source include files must use:

  ```c
  // Copyright <year> <copyright holder>
  // SPDX-License-Identifier: GPL-3.0-or-later
  ```

- New Abralia-authored desktop, host-tool, protocol, and experiment source
  files must use the comment syntax appropriate to the language and:

  ```text
  Copyright <year> <copyright holder>
  SPDX-License-Identifier: Apache-2.0
  ```

- Keep shebangs as the first line of executable scripts and place the notice
  immediately after the shebang.
- Formats that do not permit comments, including strict JSON, are covered by
  the directory rules in `LICENSE.md`; do not add invalid comment syntax.

## Upstream and GPL preservation

- Never remove or replace Keychron, QMK, ChibiOS, or other third-party
  copyright, attribution, warranty, or license notices.
- Add a modification notice with the correct copyright holder only for
  copyrightable additions. Do not claim ownership of unchanged upstream material.
- Keep firmware dependencies GPL-compatible. Do not copy GPL-covered firmware
  implementation code into the Apache-2.0 desktop tree.
- Desktop software may communicate with firmware through the documented USB
  protocol. If code is copied, shared, linked, or generated across the
  firmware/desktop license boundary, stop and review the resulting license
  obligations before continuing.
- Do not publish a firmware binary unless the exact Abralia source, pinned
  upstream source revision and submodules, build instructions, retained
  notices, and other required corresponding-source material are available for
  that release.

## Documentation

- Every public README that describes a component must state its applicable
  license or link to the repository-root `LICENSE.md`.
- Record third-party code and assets with their source, version, license, and
  retained notice when they are added.
