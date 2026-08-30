# Abralia firmware

Firmware for Abralia's QMK-compatible hardware lives in `qmk-userspace/`.

Firmware targets:

| Keyboard | Status |
| --- | --- |
| `keychron/v3_8k/ansi_encoder` | Reference hardware |
| `keychron/v3/ansi` | Experimental; hardware-unverified |
| `keychron/v3/ansi_encoder` | Experimental; hardware-unverified |

Each target provides `abralia` (RGB-only) and `abralia_host_interaction`
(RGB plus Host Interaction protocol v2). Original V3 targets retain their
upstream keymaps and board drivers; they are not V3 8K firmware images.

See the [build guide](qmk-userspace/README.md) for exact commands and the
original V3 desktop-support limitation. Offline firmware tests live in
[`tests/`](tests/README.md).

## License

Abralia-authored firmware is licensed under GPL-3.0-or-later. Upstream files
retain their original notices and licenses. See the repository-root
[`LICENSE.md`](../../LICENSE.md).
