# Slot sorting and project color demo

An isolated keyboard trial, using simulated agents and the production keyboard
renderer/input driver. It does not rearrange real registered tasks or enable
sorting in the production backend.

Stop Abralia's normal backend before running the hardware trial so only one
process owns the keyboard. From the repository root, using the Python environment
with Abralia installed:

```sh
python experiments/slot-sort-demo/keyboard_demo.py \
  --profile builtin:keychron-v3-8k-ansi-encoder-effect25 \
  --mode hardware --duration 300 \
  --command-file /tmp/abralia-sort-commands.jsonl \
  --events /tmp/abralia-sort-events.jsonl
```

The event file must be new. The command file is created with owner-only
permissions; use a fresh file for each replay.

1. Double-tap the physical Agent Mode key, then hold it for about 0.8 seconds.
2. Tap the violet Escape key to switch between arrival order and project groups.
3. Project A uses blues, B uses yellow-greens, and C uses pinks. Four agents start
   in each project. Individual colors return exactly when switching back.
4. Add an agent from a separate control terminal while watching the keyboard:

```sh
printf '%s\n' '{"command":"add","project":"A"}' >> /tmp/abralia-sort-commands.jsonl
```

In grouped order, A5 occupies F5, shifts the other groups forward, and moves C4
onto page 2. In arrival order, the new agent follows all existing arrivals.
Slot IDs remain stable through every rearrangement. Rotate the knob in page
mode to inspect page 2; another mode-key hold arms navigation again.

Wait 15 seconds without navigation activity: navigation and the knob's agent
selection both end, returning the knob to page browsing. Escape only changes
sorting while navigation is armed. Other controls retain their normal backend
behavior. The white baseline is 50% of the keyboard's existing brightness cap;
the demo never increases that cap.

Stop with Ctrl-C, the duration limit, or append `{"command":"stop"}`. The driver
releases bindings and restores its saved RGB state. Restart the normal Abralia
backend afterward. Event logs distinguish host state, physical input and rendered
key samples; they do not independently prove how the keyboard looked.

The demo palette is intentionally bounded to six project families and eight
agents per family; these are not production allocation limits. Project groups
follow first-project arrival and preserve arrival order internally. Simulated
mode allows control-file `toggle_sort` for offline checks; hardware mode requires
physical Escape and never forces Agent Mode.

Offline checks:

```sh
python -m unittest discover -s experiments/slot-sort-demo -p 'test_*.py' -v
```

Abralia-authored demo code is Apache-2.0; see [LICENSE.md](../../LICENSE.md).
