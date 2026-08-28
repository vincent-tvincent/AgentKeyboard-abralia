# Keychron V3 8K OpenRGB Python experiment

This is an experimental, read-only Python client for the OpenRGB SDK. It does
not select Python as Abralia's production host language.

The environment uses Python's standard `venv` and pins `openrgb-python` 0.3.6.
The virtual environment is local to this directory and ignored by Git.

## Result on 2026-08-23

OpenRGB 1.0rc3.1 enumerated the connected Keychron V3 8K interfaces, including:

```text
3434:0F30, usage page FF60, usage 61
```

However, OpenRGB did not register the keyboard as a controllable device. No
OpenRGB color command was sent. The Python SDK cannot control a device until
the OpenRGB application itself registers it.

## Reproduce the read-only SDK probe

Start an isolated OpenRGB server from a terminal:

```sh
mkdir -p /tmp/openrgb-keychron-test
cd /tmp/openrgb-keychron-test
/Applications/OpenRGB.app/Contents/MacOS/OpenRGB \
  --localconfig --noautoconnect --server
```

In another terminal:

```sh
cd experiments/keychron-v3-8k-openrgb-python
source .venv/bin/activate
python probe_openrgb.py
```

The probe only lists SDK devices. It never calls `set_color`, `clear`,
`set_mode`, or profile-save operations.

## License

Abralia-authored experiment code is licensed under Apache-2.0. See the
repository-root [`LICENSE.md`](../../LICENSE.md).
