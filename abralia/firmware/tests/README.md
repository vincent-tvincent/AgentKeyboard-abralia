# Offline firmware port tests

From the Abralia repository root, with Python 3 and a native C11 compiler:

```sh
python3 -m unittest discover -s abralia/firmware/tests -v
```

Set `CC` to select another compiler. No keyboard, HID library, firmware flash,
or host RGB writes are involved. Temporary executables are removed after the
tests.

The tests compile and execute the actual Host Interaction implementation for
the original V3 ANSI, original V3 ANSI encoder, and reference V3 8K ANSI encoder
configurations. QMK key events, time, and outbound Raw HID are simulated.
Coverage includes:

- target-dependent matrix dimensions, encoder count, and reserved toggle;
- rejection of protocol v1, out-of-range controls, and reserved bindings;
- immediate ordinary input without a session or enabled effect 25;
- single-tap replay at the configured matrix address and double-tap activation;
- capture, held-key release across disarm, encoder capture, and heartbeat reset;
- effect-change event ordering, no automatic reactivation, force rejection,
  event retry, and acknowledgement;
- source reuse and preservation of upstream keymap includes.

These tests do not simulate electrical scanning, USB timing, RGB output, or
STM32/AT32 hardware. Also build each complete target using the
[QMK build guide](../qmk-userspace/README.md). Physical validation is separate.

## License

Firmware tests and stubs are GPL-3.0-or-later, matching the firmware they test.
See [`LICENSE.md`](../../../LICENSE.md).
