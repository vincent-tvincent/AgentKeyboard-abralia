// Copyright 2026 blue_lobster
// SPDX-License-Identifier: GPL-3.0-or-later

#pragma once

#include <stdint.h>

enum per_key_rgb_frame_operation {
  PER_KEY_RGB_FRAME_AWAIT = 0x00,
  PER_KEY_RGB_FRAME_DIRECT = 0x01,
  PER_KEY_RGB_FRAME_BEGIN = 0x02,
  PER_KEY_RGB_FRAME_COMMIT = 0x03,
};

enum per_key_rgb_frame_state {
  PER_KEY_RGB_STATE_AWAITING = 0x00,
  PER_KEY_RGB_STATE_DIRECT = 0x01,
  PER_KEY_RGB_STATE_GUARDED = 0x02,
};

enum per_key_rgb_frame_result {
  PER_KEY_RGB_RESULT_OK = 0x00,
  PER_KEY_RGB_RESULT_BUSY = 0x01,
  PER_KEY_RGB_RESULT_INVALID_STATE = 0x02,
};

enum per_key_rgb_frame_flags {
  PER_KEY_RGB_FLAG_ACTIVE_VALID = 1 << 0,
  PER_KEY_RGB_FLAG_PENDING_VALID = 1 << 1,
  PER_KEY_RGB_FLAG_BACK_BUFFER_FREE = 1 << 2,
  PER_KEY_RGB_FLAG_TRANSITION_QUEUED = 1 << 3,
};

#define PER_KEY_RGB_FRAME_VALUE_ID 0x01
#define PER_KEY_RGB_FRAME_TIMEOUT_MS 2000
