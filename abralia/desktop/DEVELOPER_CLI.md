# Keyboard discovery and profile authoring

`abralia-dev` helps developers inspect a new keyboard before a profile exists.
It collects HID matching fields, queries an explicitly chosen firmware protocol,
creates an incomplete draft, and validates the completed configuration.

Discovery, protocol support, profile validity and physical verification are
separate results. The CLI never flashes firmware, selects an RGB effect, changes
brightness, installs bindings, writes a keymap, or saves EEPROM.

## Install or run

From the repository root:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e abralia/desktop
.venv/bin/abralia-dev --help
```

The examples below use `abralia-dev` with that environment activated; otherwise
use `.venv/bin/abralia-dev`. The equivalent module entry is
`python -m abralia.developer`.

App builds containing the developer CLI also provide it through their bundled
Python executable, without a separate Python installation:

```sh
"/path/to/Abralia.app/Contents/Resources/backend/abralia-gui-host/abralia-gui-host" dev --help
```

The source CLI uses the desktop package's supported Python version and HID API.
The current app package is macOS-only; native Windows/Linux discovery depends
on that system's HID permissions and driver availability. The backend-cache
route currently uses Abralia's private Unix socket discovery.

## 1. Scan without a profile

```sh
abralia-dev scan
abralia-dev scan --vendor-id 0x3434 --product-id 0x0F30 --json
```

This enumerates HID descriptors without opening an HID handle or sending
firmware commands. It returns:

| Field | Meaning |
| --- | --- |
| `selection_id` | Exact interface identifier to pass to another command. |
| `vendor_id`, `product_id` | Numeric USB vendor/model matching fields. |
| `usage_page`, `usage` | HID usage identifying the interface's purpose. |
| `interface_number` | OS-reported interface number, when available. |
| `product`, `manufacturer`, `serial_number` | Device-reported descriptive information. |
| `hex` | Hexadecimal equivalents of the numeric matching fields. |
| `common_via_rawhid_usage` | Whether the usage is `FF60:0061`; a hint, not proof of firmware compatibility. |

Several rows can belong to one keyboard: typing, media keys and Raw HID can be
different interfaces. Choose the intended vendor control interface. The explicit
ID includes the path and descriptor identity; it is not a list index. A changed,
missing or ambiguous interface requires another scan instead of silently
selecting another device. Missing usage information remains unknown.

An empty list means no matching HID interface was visible to the process.
Check the connection and OS permissions before concluding that a device is
unsupported. DFU bootloaders are outside this HID scan.

## 2. Query an explicitly selected protocol

Replace `DEVICE_ID` below with the `selection_id` from your scan:

```sh
abralia-dev probe --device DEVICE_ID \
  --protocol abralia-keychron --json --output keyboard-probe.json
```

Protocol choice is explicit because USB IDs do not identify every device's
command language. Choose only a protocol implemented by the selected firmware:

| Protocol | Information queried |
| --- | --- |
| `abralia-v2` | Host Interaction version, matrix dimensions, encoder count, control count, supported binding/lifetime flags and timing limits. |
| `keychron-rgb` | Keychron/RGB protocol version and LED count; an optional effect-25 frame-status query checks that extension. |
| `abralia-keychron` | Both groups, for an Abralia Host Interaction Keychron firmware. |

These initial protocol probes require the `FF60:0061` Raw HID interface. Selecting
a normal typing interface is rejected. Unsupported or inconsistent responses
are errors; an unrecognized optional frame query remains unknown. Session tokens
are never included in the report.

### Probe sources and keyboard ownership

`--source auto` is the default:

- When Abralia already owns the selected device, the CLI requests capabilities
  cached by that backend. No second HID handle or new firmware query is opened.
  The report is labelled `source: backend_cache`, with its observation boundary.
- If no matching Abralia owner is present, the CLI opens only the explicitly
  selected interface and sends the protocol's fixed information queries. It
  closes that handle afterward and labels the result `source: device_query`.
- An uncertain owner, an unresponsive backend, or an older backend that cannot
  supply the cache stops the probe. It never silently falls back to competing
  direct access.

Use `--source backend` to require the cache. Use `--source direct` for standalone
firmware development after quitting Abralia and closing other HID controllers,
including VIA/Keychron Launcher. Direct mode still refuses a detected Abralia
owner; it is not a force/takeover switch. Host Interaction firmware reporting an
active session stops further queries.

`--runtime-dir /path/to/private/runtime` selects a custom Abralia discovery
directory, useful for isolated development. A running backend with no trustworthy
device identity must be stopped or updated before direct probing proceeds.

Information queries use USB output reports to request replies, but they do not
issue configuration or lighting setters. Cached results reflect checks made
when the backend opened the device, not a fresh measurement of every capability.

## 3. Generate a draft

USB-only drafting needs no firmware probe:

```sh
abralia-dev profile draft --device DEVICE_ID \
  --id my-keyboard --name "My keyboard" --output my-keyboard.draft.json
```

To include the report from step 2:

```sh
abralia-dev profile draft --device DEVICE_ID \
  --probe-report keyboard-probe.json --id my-keyboard \
  --name "My keyboard" --output my-keyboard.draft.json
```

The report must match the exact currently scanned interface. Imported report
data is retained as evidence, not treated as a signed hardware certification.

The draft has its own `abralia-device-profile-draft` format and wraps the editable
profile in a `profile` object. Unreported values are `null`; keys and regions
start empty. `missing_fields` explains the work remaining. A draft cannot be
loaded directly as a production profile.

Developers still supply or verify:

- Physical key geometry and matrix-to-key labels.
- LED addresses and LED coordinates.
- Regions and any semantic/alias mappings.
- The firmware-reserved mode-key position.
- Capabilities not exposed by the chosen protocol, and the matching adapter.

Use the matching firmware's source/layout metadata, followed by device checks.
The existing [control inspector](../../experiments/host-interaction-control-inspector/README.md)
can help verify physical input after a suitable profile is available. It is an
interactive tool that installs temporary bindings; passive discovery does not
run it automatically.

For an intentionally RGB-only profile, omit the `interaction` section instead
of inventing a mode-key position. Profile dimensions and adapter information
must describe the actual firmware, not whichever bundled keyboard looks similar.

All output options create new files and refuse to overwrite existing files or
symlinks. Choose a new output name when replaying the workflow.

## 4. Validate and export

After completing the draft's `profile` object:

```sh
abralia-dev profile validate my-keyboard.draft.json --for-agent --json
abralia-dev profile validate my-keyboard.draft.json --for-agent \
  --output my-keyboard.json
```

Export occurs only when validation succeeds. The exported file contains the
native profile data, without the draft envelope or discovery report.

Default validation checks the RGB profile schema and mapping invariants.
`--for-agent` additionally checks the current backend's physical control and
mode-position requirements. Omit it when authoring an RGB-only profile.
Built-in profiles can also be checked:

```sh
abralia-dev profile validate builtin:keychron-v3-8k-ansi-encoder-effect25 --for-agent
```

Configuration validation does not open hardware and always reports
`hardware_verified: false`. It does not establish actual LED placement, key
capture, USB compatibility or firmware timing. Run simulator and physical
acceptance checks before publishing support for a new model.

The desktop APIs accept a completed profile through an explicit JSON path.
For the current GUI, add a reviewed profile to
`abralia/desktop/src/abralia/resources/profiles/`, register its ID in
`backend/gui_devices.py`'s `SUPPORTED_PROFILES`, then rebuild the app. A user
profile-folder/import feature is not part of this CLI. Different firmware
protocols may require a new adapter; JSON does not implement a driver.

See the [profile catalog and format](src/abralia/resources/profiles/README.md),
[RGB API guide](README.md), and [Host Interaction guide](HOST_INTERACTION_API.md).

## Automation and exit codes

Every command accepts `--json`, including after its subcommand. Operational
errors produce a structured error object in JSON mode. Argument syntax errors
use argparse's normal stderr/exit behavior. Output reports include a format and
version so other tools can inspect them deliberately.

| Exit | Meaning |
| --- | --- |
| `0` | Command completed, including creating an explicitly incomplete draft. |
| `2` | Invalid input, invalid/incomplete profile, or refused output overwrite. |
| `3` | Device selection/ownership is unavailable, ambiguous or unsafe to resolve. |
| `4` | Protocol, transport or another execution error. |

Example: validate a draft and use its JSON `errors` array to show missing fields
in an editor. Never treat `profile draft` returning zero as hardware readiness.

Abralia-authored tools and documentation are Apache-2.0; see
[LICENSE.md](../../LICENSE.md).
