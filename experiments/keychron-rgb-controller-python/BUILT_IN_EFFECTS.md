# Keychron V3 8K built-in RGB effects

This document records the built-in RGB Matrix effects exposed by the stock
Keychron V3 8K ANSI encoder firmware and its Launcher/VIA definition. The IDs
are verified for this exact `3434:0F30` board and should not be assumed for a
different model without checking that model's definition.

The descriptions below summarize the expected visual behavior represented by
the QMK/Keychron effect names. Exact appearance depends on the keyboard's LED
positions, brightness, speed, firmware revision, and keycaps.

## Effect list

| ID | Launcher name | Explanation | Standard speed | Standard color |
| ---: | --- | --- | :---: | :---: |
| 0 | None | Turns the RGB Matrix effect off. | No | No |
| 1 | Solid Color | Illuminates the whole keyboard with one hue and saturation. | No | Yes |
| 2 | Breathing | Repeatedly raises and lowers the brightness of one selected color. | Yes | Yes |
| 3 | Band Spiral Val | Moves spiral-shaped brightness bands around the keyboard using the selected color. | Yes | Yes |
| 4 | Cycle All | Cycles the entire keyboard through the hue spectrum. | Yes | No |
| 5 | Cycle Left Right | Moves a rainbow or hue gradient horizontally across the keyboard. | Yes | No |
| 6 | Cycle Up Down | Moves a rainbow or hue gradient vertically across the keyboard. | Yes | No |
| 7 | Rainbow Moving Chevron | Moves a chevron-shaped rainbow pattern across the keyboard. | Yes | No |
| 8 | Cycle Out In | Cycles colors radially between the center and outer edges. | Yes | No |
| 9 | Cycle Out In Dual | Runs two complementary out/in radial color cycles. | Yes | No |
| 10 | Cycle Pinwheel | Rotates a pinwheel-shaped rainbow pattern around the keyboard. | Yes | No |
| 11 | Cycle Spiral | Rotates a continuous spiral hue pattern. | Yes | No |
| 12 | Dual Beacon | Rotates two opposing beacon-like color bands. | Yes | No |
| 13 | Rainbow Beacon | Rotates a multicolor beacon pattern around the keyboard. | Yes | No |
| 14 | Jellybean Raindrops | Places changing, candy-like random colors across individual LEDs. | Yes | No |
| 15 | Pixel Rain | Produces moving or falling colored pixels across the matrix. | Yes | No |
| 16 | Typing Heatmap | Builds a heat-style visualization from recently pressed keys and lets it cool over time. | Yes | No |
| 17 | Digital Rain | Produces a matrix-like falling-rain pattern. | Yes | No |
| 18 | Reactive Simple | Lights the pressed key with the selected color and fades it. | Yes | Yes |
| 19 | Reactive Multiwide | Produces a wider selected-color response around recently pressed keys. | Yes | Yes |
| 20 | Reactive Multinexus | Produces intersecting or nexus-like selected-color reactions around keypresses. | Yes | Yes |
| 21 | Splash | Sends a multicolor splash/ripple outward from a pressed key. | Yes | No |
| 22 | Solid Splash | Sends a splash/ripple in one selected color from a pressed key. | Yes | Yes |
| 23 | Per Key RGB | Uses the Keychron per-key HSV buffer so each LED can have its own color. It also has separate subtypes described below. | Custom | Per key |
| 24 | Mix RGB | Assigns LEDs to regions and combines configured effects in those regions. | Per region | Per region |

`Standard speed` and `Standard color` describe controls exposed by the V3 8K
Launcher/VIA lighting menu. Effects 2 through 22 expose the standard speed
slider. The standard color picker appears for effects 1–3, 18–20, and 22.

## Effect 23: Per Key RGB subtypes

The Keychron RGB protocol exposes a separate per-key type byte:

| Type | Firmware enum | Explanation |
| ---: | --- | --- |
| 0 | `PER_KEY_RGB_SOLID` | Displays every LED's stored hue and saturation continuously. This is the subtype used by the Python demos. |
| 1 | `PER_KEY_RGB_BREATHING` | Breathes the per-key colors using the global RGB speed and brightness. |
| 2 | `PER_KEY_RGB_REATIVE_SIMPLE` | Uses each key's stored color for a simple press-and-fade reaction. |
| 3 | `PER_KEY_RGB_REATIVE_MULTI_WIDE` | Uses stored colors for a wider multi-key reaction. |
| 4 | `PER_KEY_RGB_REATIVE_SPLASH` | Uses stored colors in a splash reaction from recent keypresses. |

The misspelling `REATIVE` is present in the upstream firmware enum and is
reproduced here so the names can be searched exactly in source.

### Stock per-key brightness limitation

The stock solid per-key renderer reads each LED's stored HSV value but replaces
the stored V component with global brightness. Consequences:

- independent per-key hue and saturation work;
- global brightness works;
- stored per-key `V=0` does not reliably turn one LED off;
- a white background with colored foreground keys works;
- a black background with colored foreground keys requires a later firmware
  change.

This is why the capability-driven demo reports the stock board as Level 2
instead of Level 3.

## Effect 24: Mix RGB

Mix RGB stores a region number for every LED and a list of effects for each
region. Each region effect can contain:

```text
effect ID
hue
saturation
speed
duration/time
```

This is more complex than selecting one built-in effect. A host controller
should snapshot the region and effect-list configuration before experimenting
and should not persist a modified Mix RGB profile without explicit user intent.

## Host controls

The standard VIA RGB Matrix channel is channel `3`. These value IDs are used by
the stock firmware:

| Value ID | Control | Data |
| ---: | --- | --- |
| 1 | Brightness | One byte, `0...255` |
| 2 | Effect | One byte, effect ID `0...24` |
| 3 | Speed | One byte, `0...255` |
| 4 | Color | Two bytes: hue and saturation, each `0...255` |

The relevant VIA command IDs are:

```text
0x07  Set a value in RAM
0x08  Read a value
0x09  Save the selected value persistently
```

Abralia's experiments use only volatile set/read operations. They intentionally
do not send `0x09` or Keychron RGB `RGB_SAVE`.

Conceptually, selecting Rainbow Beacon temporarily is:

```text
VIA custom set
channel = RGB Matrix (3)
value = effect (2)
data = Rainbow Beacon (13)
```

Brightness and speed can be changed through their value IDs. Effects with a
standard color control accept hue and saturation through value ID 4.

## Recommended experiment behavior

A safe built-in-effect demo should:

1. discover and capability-check the exact device;
2. read the current effect, brightness, speed, hue, and saturation;
3. set the requested effect with volatile commands;
4. display it for a bounded duration;
5. restore every captured value;
6. read the values back and confirm restoration;
7. never save to EEPROM unless the user explicitly requests persistence.

Keychron Launcher and VIA should be closed while the experiment owns the Raw
HID interface.
