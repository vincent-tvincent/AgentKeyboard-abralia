# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Optional local simulator frame callback; it never uses hardware or networking."""

import math


def frame(elapsed, profile, state):
    level = round(90 + 90 * (1 + math.sin(elapsed * math.tau / 3)) / 2)
    color = f'{level:02X}{level:02X}{level:02X}'
    return {'colors': {key: color for key in ('W', 'A', 'S', 'D')}}


render_frame = frame
