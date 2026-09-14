// Copyright 2026 blue_lobster
// SPDX-License-Identifier: GPL-3.0-or-later

#include QMK_KEYBOARD_H

#include <string.h>

#include "per_key_rgb_independent_v.h"
#include "rgb_matrix.h"
#include "timer.h"
#include "via.h"
#include <lib/lib8tion/lib8tion.h>

#define IDLE_HALO_DURATION_MS 10000
#define IDLE_HALO_FRAME_INTERVAL_MS 50
#define IDLE_HALO_RADIUS 60
#define IDLE_HALO_MAX_BRIGHTNESS 112
#define IDLE_HALO_MIN_VISIBLE_VALUE 4

_Static_assert(RGB_MATRIX_CUSTOM_PER_KEY_RGB == 23,
               "Keychron PER_KEY_RGB effect ID changed");
_Static_assert(RGB_MATRIX_CUSTOM_MIXED_RGB == 24,
               "Keychron MIXED_RGB effect ID changed");
_Static_assert(RGB_MATRIX_CUSTOM_PER_KEY_RGB_INDEPENDENT_V == 25,
               "Independent-V effect must be ID 25");
_Static_assert(RGB_MATRIX_EFFECT_MAX == 26,
               "Unexpected RGB Matrix effect appended after effect 25");
_Static_assert(RGB_MATRIX_DEFAULT_MODE ==
                   RGB_MATRIX_CUSTOM_PER_KEY_RGB_INDEPENDENT_V,
               "Independent-V effect must be the RGB Matrix default");

extern HSV per_key_led[RGB_MATRIX_LED_COUNT];

static const HSV idle_halo_palette[] = {
    {4, 235, 255},   // Coral red
    {92, 160, 255},  // Mint green
    {160, 175, 255}, // Soft azure
    {35, 235, 255},  // Warm amber
};

typedef enum {
  TRANSITION_NONE,
  TRANSITION_AWAIT,
  TRANSITION_DIRECT,
  TRANSITION_BEGIN,
} frame_transition_t;

static HSV display_frames[2][RGB_MATRIX_LED_COUNT];
static uint8_t display_frame_max[2];
static uint8_t front_frame_index;
static uint8_t back_frame_index = 1;

static uint8_t frame_state = PER_KEY_RGB_STATE_AWAITING;
static uint8_t active_sequence;
static uint8_t pending_sequence;
static bool front_frame_valid;
static bool active_sequence_valid;
static bool pending_frame_valid;
static bool effect_selected;

static frame_transition_t queued_transition;
static uint32_t last_guarded_activity;
static uint8_t render_frame_max;
static uint8_t render_global_value;

static uint32_t idle_halo_started_at;
static uint8_t idle_halo_center = UINT8_MAX;
static uint8_t idle_halo_last_step = UINT8_MAX;
static uint8_t idle_halo_last_global = UINT8_MAX;
static uint8_t idle_halo_master;
static RGB idle_halo_rgb;
static bool idle_halo_ready;
static bool idle_halo_entropy_added;

static uint8_t maximum_value(const HSV *frame) {
  uint8_t maximum = 0;

  for (uint8_t i = 0; i < RGB_MATRIX_LED_COUNT; i++) {
    if (frame[i].v > maximum) {
      maximum = frame[i].v;
    }
  }

  return maximum;
}

static void reset_idle_halo(void) {
  idle_halo_ready = false;
  idle_halo_last_step = UINT8_MAX;
  idle_halo_last_global = UINT8_MAX;
  idle_halo_master = 0;
  idle_halo_rgb = (RGB){0, 0, 0};
}

static void clear_display_frames(void) {
  memset(display_frames, 0, sizeof(display_frames));
  memset(display_frame_max, 0, sizeof(display_frame_max));
  front_frame_index = 0;
  back_frame_index = 1;
  front_frame_valid = false;
  active_sequence_valid = false;
  pending_frame_valid = false;
  active_sequence = 0;
  pending_sequence = 0;
  reset_idle_halo();
}

static void reset_to_awaiting(void) {
  clear_display_frames();
  frame_state = PER_KEY_RGB_STATE_AWAITING;
  queued_transition = TRANSITION_NONE;
}

static void synchronize_effect_selection(void) {
  bool selected =
      rgb_matrix_get_mode() == RGB_MATRIX_CUSTOM_PER_KEY_RGB_INDEPENDENT_V;

  if (selected != effect_selected) {
    effect_selected = selected;
    reset_to_awaiting();
  }
}

static void enter_guarded_mode(uint32_t now) {
  bool preserve_direct_frame = frame_state == PER_KEY_RGB_STATE_DIRECT;

  clear_display_frames();
  if (preserve_direct_frame) {
    memcpy(display_frames[front_frame_index], per_key_led, sizeof(per_key_led));
    display_frame_max[front_frame_index] =
        maximum_value(display_frames[front_frame_index]);
    front_frame_valid = true;
  }

  frame_state = PER_KEY_RGB_STATE_GUARDED;
  last_guarded_activity = now;
}

static void apply_queued_transition(uint32_t now) {
  frame_transition_t transition = queued_transition;
  queued_transition = TRANSITION_NONE;

  switch (transition) {
  case TRANSITION_AWAIT:
    reset_to_awaiting();
    break;

  case TRANSITION_DIRECT:
    clear_display_frames();
    frame_state = PER_KEY_RGB_STATE_DIRECT;
    break;

  case TRANSITION_BEGIN:
    enter_guarded_mode(now);
    break;

  case TRANSITION_NONE:
    break;
  }
}

static uint8_t normalized_value(uint8_t input_value) {
  if (input_value == 0 || render_global_value == 0 || render_frame_max == 0) {
    return 0;
  }

  uint8_t output_value =
      ((uint16_t)input_value * render_global_value + render_frame_max / 2) /
      render_frame_max;
  return output_value == 0 ? 1 : output_value;
}

static uint8_t doubled_positive_sine(uint8_t phase) {
  uint8_t wave = qsub8(sin8(phase), 128);
  return qadd8(wave, wave);
}

static uint8_t idle_halo_falloff(uint8_t distance) {
  if (distance >= IDLE_HALO_RADIUS) {
    return 0;
  }
  if (distance == 0) {
    return 255;
  }

  uint8_t phase =
      64 + ((uint16_t)distance * 64 + IDLE_HALO_RADIUS / 2) / IDLE_HALO_RADIUS;
  uint8_t cosine = doubled_positive_sine(phase);
  uint8_t cosine_squared = scale8(cosine, cosine);
  // Host halo power 1.5 maps to cosine cubed in this integer formulation.
  return scale8(cosine_squared, cosine);
}

static void prepare_idle_halo(uint32_t now) {
  if (!idle_halo_entropy_added) {
    random16_add_entropy((uint16_t)now);
    idle_halo_entropy_added = true;
  }

  uint8_t center = random8_max(RGB_MATRIX_LED_COUNT);
  while (RGB_MATRIX_LED_COUNT > 1 && center == idle_halo_center) {
    center = random8_max(RGB_MATRIX_LED_COUNT);
  }
  idle_halo_center = center;

  uint8_t palette_index =
      random8_max(sizeof(idle_halo_palette) / sizeof(idle_halo_palette[0]));
  idle_halo_rgb = hsv_to_rgb(idle_halo_palette[palette_index]);

  int16_t center_x = g_led_config.point[center].x;
  int16_t center_y = g_led_config.point[center].y;
  for (uint8_t i = 0; i < RGB_MATRIX_LED_COUNT; i++) {
    int16_t dx = g_led_config.point[i].x - center_x;
    int16_t dy = g_led_config.point[i].y - center_y;
    uint8_t distance = sqrt16(dx * dx + dy * dy);

    display_frames[0][i].h = 0;
    display_frames[0][i].s = 0;
    display_frames[0][i].v = idle_halo_falloff(distance);
  }

  idle_halo_started_at = now;
  idle_halo_last_step = UINT8_MAX;
  idle_halo_ready = true;
}

static bool update_idle_halo(uint32_t now, bool force) {
  uint32_t elapsed = idle_halo_ready ? now - idle_halo_started_at : 0;
  if (!idle_halo_ready || elapsed >= IDLE_HALO_DURATION_MS) {
    prepare_idle_halo(now);
    elapsed = 0;
    force = true;
  }

  uint8_t step = elapsed / IDLE_HALO_FRAME_INTERVAL_MS;
  uint8_t global_value = rgb_matrix_config.hsv.v;
  if (global_value > IDLE_HALO_MAX_BRIGHTNESS) {
    global_value = IDLE_HALO_MAX_BRIGHTNESS;
  }

  if (!force && step == idle_halo_last_step &&
      global_value == idle_halo_last_global) {
    return false;
  }

  idle_halo_last_step = step;
  idle_halo_last_global = global_value;

  uint8_t phase = ((uint32_t)elapsed * 128 + IDLE_HALO_DURATION_MS / 2) /
                  IDLE_HALO_DURATION_MS;
  uint8_t wave = doubled_positive_sine(phase);
  uint8_t breath = scale8(wave, wave);
  idle_halo_master = scale8(breath, global_value);
  return true;
}

static void render_idle_halo_led(uint8_t index) {
  uint8_t effective_value =
      scale8(display_frames[0][index].v, idle_halo_master);
  if (effective_value < IDLE_HALO_MIN_VISIBLE_VALUE) {
    rgb_matrix_set_color(index, 0, 0, 0);
    return;
  }

  rgb_matrix_set_color(index, scale8(idle_halo_rgb.r, effective_value),
                       scale8(idle_halo_rgb.g, effective_value),
                       scale8(idle_halo_rgb.b, effective_value));
}

static uint8_t status_flags(void) {
  uint8_t flags = 0;

  if (active_sequence_valid) {
    flags |= PER_KEY_RGB_FLAG_ACTIVE_VALID;
  }
  if (pending_frame_valid) {
    flags |= PER_KEY_RGB_FLAG_PENDING_VALID;
  }
  if (frame_state == PER_KEY_RGB_STATE_GUARDED && !pending_frame_valid &&
      queued_transition == TRANSITION_NONE) {
    flags |= PER_KEY_RGB_FLAG_BACK_BUFFER_FREE;
  }
  if (queued_transition != TRANSITION_NONE) {
    flags |= PER_KEY_RGB_FLAG_TRANSITION_QUEUED;
  }

  return flags;
}

static void write_status_response(uint8_t *data, uint8_t result) {
  data[3] = frame_state;
  data[4] = active_sequence;
  data[5] = pending_sequence;
  data[6] = status_flags();
  data[7] = result;
}

static uint8_t request_transition(frame_transition_t transition) {
  if (!effect_selected) {
    return PER_KEY_RGB_RESULT_INVALID_STATE;
  }
  if (queued_transition != TRANSITION_NONE) {
    return PER_KEY_RGB_RESULT_BUSY;
  }
  if (transition == TRANSITION_BEGIN &&
      frame_state == PER_KEY_RGB_STATE_GUARDED) {
    return PER_KEY_RGB_RESULT_INVALID_STATE;
  }

  queued_transition = transition;
  return PER_KEY_RGB_RESULT_OK;
}

static uint8_t commit_frame(uint8_t sequence) {
  if (!effect_selected || frame_state != PER_KEY_RGB_STATE_GUARDED ||
      queued_transition != TRANSITION_NONE) {
    return PER_KEY_RGB_RESULT_INVALID_STATE;
  }
  if (pending_frame_valid) {
    return PER_KEY_RGB_RESULT_BUSY;
  }

  memcpy(display_frames[back_frame_index], per_key_led, sizeof(per_key_led));
  display_frame_max[back_frame_index] =
      maximum_value(display_frames[back_frame_index]);
  pending_sequence = sequence;
  pending_frame_valid = true;
  last_guarded_activity = timer_read32();
  return PER_KEY_RGB_RESULT_OK;
}

void via_custom_value_command_kb(uint8_t *data, uint8_t length) {
  synchronize_effect_selection();

  if (length < 8 || data[1] != id_custom_channel ||
      data[2] != PER_KEY_RGB_FRAME_VALUE_ID) {
    data[0] = id_unhandled;
    return;
  }

  uint8_t result = PER_KEY_RGB_RESULT_OK;

  switch (data[0]) {
  case id_custom_get_value:
    break;

  case id_custom_set_value: {
    uint8_t operation = data[3];
    uint8_t sequence = data[4];

    switch (operation) {
    case PER_KEY_RGB_FRAME_AWAIT:
      result = request_transition(TRANSITION_AWAIT);
      break;

    case PER_KEY_RGB_FRAME_DIRECT:
      result = request_transition(TRANSITION_DIRECT);
      break;

    case PER_KEY_RGB_FRAME_BEGIN:
      result = request_transition(TRANSITION_BEGIN);
      break;

    case PER_KEY_RGB_FRAME_COMMIT:
      result = commit_frame(sequence);
      break;

    default:
      result = PER_KEY_RGB_RESULT_INVALID_STATE;
      break;
    }
  } break;

  default:
    data[0] = id_unhandled;
    return;
  }

  write_status_response(data, result);
}

void housekeeping_task_user(void) { synchronize_effect_selection(); }

bool per_key_rgb_independent_v(effect_params_t *params) {
  synchronize_effect_selection();
  RGB_MATRIX_USE_LIMITS(led_min, led_max);

  if (params->iter == 0) {
    uint32_t now = timer_read32();

    apply_queued_transition(now);

    if (frame_state == PER_KEY_RGB_STATE_GUARDED &&
        timer_elapsed32(last_guarded_activity) >=
            PER_KEY_RGB_FRAME_TIMEOUT_MS) {
      reset_to_awaiting();
    }

    if (frame_state == PER_KEY_RGB_STATE_GUARDED && pending_frame_valid) {
      uint8_t old_front = front_frame_index;
      front_frame_index = back_frame_index;
      back_frame_index = old_front;

      active_sequence = pending_sequence;
      active_sequence_valid = true;
      front_frame_valid = true;
      pending_frame_valid = false;
    }

    if (frame_state == PER_KEY_RGB_STATE_AWAITING) {
      if (!update_idle_halo(now, params->init)) {
        return false;
      }
    } else if (frame_state == PER_KEY_RGB_STATE_DIRECT) {
      render_global_value = rgb_matrix_config.hsv.v;
      render_frame_max = maximum_value(per_key_led);
    } else if (frame_state == PER_KEY_RGB_STATE_GUARDED && front_frame_valid) {
      render_global_value = rgb_matrix_config.hsv.v;
      render_frame_max = display_frame_max[front_frame_index];
    } else {
      render_global_value = rgb_matrix_config.hsv.v;
      render_frame_max = 0;
    }
  }

  for (uint8_t i = led_min; i < led_max; i++) {
    if (frame_state == PER_KEY_RGB_STATE_AWAITING) {
      render_idle_halo_led(i);
      continue;
    }

    HSV hsv = {0, 0, 0};

    if (frame_state == PER_KEY_RGB_STATE_DIRECT) {
      hsv = per_key_led[i];
    } else if (frame_state == PER_KEY_RGB_STATE_GUARDED && front_frame_valid) {
      hsv = display_frames[front_frame_index][i];
    }

    hsv.v = normalized_value(hsv.v);
    RGB rgb = hsv_to_rgb(hsv);
    rgb_matrix_set_color(i, rgb.r, rgb.g, rgb.b);
  }

  return rgb_matrix_check_finished_leds(led_max);
}
