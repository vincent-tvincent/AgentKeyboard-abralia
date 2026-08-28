// Copyright 2026 blue_lobster
// SPDX-License-Identifier: GPL-3.0-or-later

#include QMK_KEYBOARD_H

#include <string.h>

#include "action.h"
#include "host_interaction.h"
#include "host_interaction_protocol.h"
#include "timer.h"

#if defined(ENCODER_ENABLE)
#define HOST_INTERACTION_ENCODER_CONTROL_COUNT (NUM_ENCODERS * 2)
#else
#define HOST_INTERACTION_ENCODER_CONTROL_COUNT 0
#endif

#define HOST_INTERACTION_MATRIX_CONTROL_COUNT (MATRIX_ROWS * MATRIX_COLS)
#define HOST_INTERACTION_CONTROL_COUNT                                         \
  (HOST_INTERACTION_MATRIX_CONTROL_COUNT +                                     \
   HOST_INTERACTION_ENCODER_CONTROL_COUNT)
#define HOST_INTERACTION_PAUSE_ROW 0
#define HOST_INTERACTION_PAUSE_COL 16
#define HOST_INTERACTION_PAUSE_CONTROL                                         \
  HOST_INTERACTION_CONTROL_ID(HOST_INTERACTION_CONTROL_KEY,                    \
                              HOST_INTERACTION_PAUSE_ROW,                      \
                              HOST_INTERACTION_PAUSE_COL)

typedef enum {
  PAUSE_GESTURE_IDLE,
  PAUSE_GESTURE_FIRST_DOWN,
  PAUSE_GESTURE_WAIT_SECOND,
  PAUSE_GESTURE_SECOND_DOWN,
  PAUSE_GESTURE_PASSTHROUGH_HELD,
} pause_gesture_state_t;

typedef struct {
  bool active;
  host_interaction_resolved_binding_t binding;
} host_interaction_press_latch_t;

static host_interaction_press_latch_t
    press_latches[HOST_INTERACTION_CONTROL_COUNT];
static pause_gesture_state_t pause_gesture_state;
static uint32_t pause_gesture_started_at;
static bool pause_replay_bypass;

static bool control_id_from_record(const keyrecord_t *record,
                                   uint16_t *control_id) {
  if (IS_KEYEVENT(record->event)) {
    *control_id = HOST_INTERACTION_CONTROL_ID(HOST_INTERACTION_CONTROL_KEY,
                                              record->event.key.row,
                                              record->event.key.col);
    return true;
  }

#if defined(ENCODER_ENABLE)
  if (record->event.type == ENCODER_CW_EVENT) {
    *control_id = HOST_INTERACTION_CONTROL_ID(
        HOST_INTERACTION_CONTROL_ENCODER_CW, record->event.key.col, 0);
    return true;
  }
  if (record->event.type == ENCODER_CCW_EVENT) {
    *control_id = HOST_INTERACTION_CONTROL_ID(
        HOST_INTERACTION_CONTROL_ENCODER_CCW, record->event.key.col, 0);
    return true;
  }
#endif

  return false;
}

static bool control_index(uint16_t control_id, uint16_t *index) {
  uint8_t kind = HOST_INTERACTION_CONTROL_KIND(control_id);
  uint8_t primary = HOST_INTERACTION_CONTROL_PRIMARY(control_id);
  uint8_t secondary = HOST_INTERACTION_CONTROL_SECONDARY(control_id);

  if (kind == HOST_INTERACTION_CONTROL_KEY && primary < MATRIX_ROWS &&
      secondary < MATRIX_COLS) {
    *index = (uint16_t)primary * MATRIX_COLS + secondary;
    return true;
  }

#if defined(ENCODER_ENABLE)
  if ((kind == HOST_INTERACTION_CONTROL_ENCODER_CW ||
       kind == HOST_INTERACTION_CONTROL_ENCODER_CCW) &&
      primary < NUM_ENCODERS && secondary == 0) {
    *index = HOST_INTERACTION_MATRIX_CONTROL_COUNT + (uint16_t)primary * 2 +
             (kind == HOST_INTERACTION_CONTROL_ENCODER_CCW ? 1 : 0);
    return true;
  }
#endif

  return false;
}

static void replay_pause_event(bool pressed) {
  pause_replay_bypass = true;
  action_exec(MAKE_KEYEVENT(HOST_INTERACTION_PAUSE_ROW,
                            HOST_INTERACTION_PAUSE_COL, pressed));
  pause_replay_bypass = false;
}

static void replay_pause_tap(void) {
  replay_pause_event(true);
  replay_pause_event(false);
}

static void flush_pause_before_intervening_event(void) {
  switch (pause_gesture_state) {
  case PAUSE_GESTURE_FIRST_DOWN:
    replay_pause_event(true);
    pause_gesture_state = PAUSE_GESTURE_PASSTHROUGH_HELD;
    break;

  case PAUSE_GESTURE_WAIT_SECOND:
    replay_pause_tap();
    pause_gesture_state = PAUSE_GESTURE_IDLE;
    break;

  case PAUSE_GESTURE_IDLE:
  case PAUSE_GESTURE_SECOND_DOWN:
  case PAUSE_GESTURE_PASSTHROUGH_HELD:
    break;
  }
}

static bool handle_pause_gesture(const keyrecord_t *record) {
  if (pause_gesture_state == PAUSE_GESTURE_PASSTHROUGH_HELD) {
    if (!record->event.pressed) {
      pause_gesture_state = PAUSE_GESTURE_IDLE;
    }
    return true;
  }

  if (!host_interaction_protocol_session_alive()) {
    return true;
  }

  uint32_t now = timer_read32();
  switch (pause_gesture_state) {
  case PAUSE_GESTURE_IDLE:
    if (!record->event.pressed) {
      return true;
    }
    pause_gesture_state = PAUSE_GESTURE_FIRST_DOWN;
    pause_gesture_started_at = now;
    return false;

  case PAUSE_GESTURE_FIRST_DOWN:
    if (!record->event.pressed) {
      pause_gesture_state = PAUSE_GESTURE_WAIT_SECOND;
      pause_gesture_started_at = now;
    }
    return false;

  case PAUSE_GESTURE_WAIT_SECOND:
    if (!record->event.pressed) {
      return false;
    }
    if (timer_elapsed32(pause_gesture_started_at) <=
        HOST_INTERACTION_DOUBLE_TAP_TERM_MS) {
      pause_gesture_state = PAUSE_GESTURE_SECOND_DOWN;
      return false;
    }

    replay_pause_tap();
    pause_gesture_state = PAUSE_GESTURE_FIRST_DOWN;
    pause_gesture_started_at = now;
    return false;

  case PAUSE_GESTURE_SECOND_DOWN:
    if (!record->event.pressed) {
      pause_gesture_state = PAUSE_GESTURE_IDLE;
      host_interaction_protocol_toggle_manual_mode();
    }
    return false;

  case PAUSE_GESTURE_PASSTHROUGH_HELD:
    return true;
  }

  return true;
}

static bool route_bound_control(uint16_t control_id,
                                const keyrecord_t *record) {
  uint16_t index;
  if (!control_index(control_id, &index)) {
    return true;
  }

  if (record->event.pressed) {
    host_interaction_resolved_binding_t binding;
    if (!host_interaction_protocol_resolve_binding(control_id, &binding)) {
      return true;
    }

    bool should_send =
        (binding.flags & HOST_INTERACTION_BINDING_EVENT_DOWN) != 0;
    if (should_send && !host_interaction_protocol_enqueue_control_edge(
                           control_id, &binding, true, false)) {
      return true;
    }

    press_latches[index] = (host_interaction_press_latch_t){
        .active = true,
        .binding = binding,
    };
    return (binding.flags & HOST_INTERACTION_BINDING_MIRROR) != 0;
  }

  host_interaction_press_latch_t latch = press_latches[index];
  if (!latch.active) {
    return true;
  }

  bool one_shot = latch.binding.lifetime == HOST_INTERACTION_LIFETIME_ONE_SHOT;
  if (host_interaction_protocol_session_alive() &&
      (latch.binding.flags & HOST_INTERACTION_BINDING_EVENT_UP) != 0) {
    host_interaction_protocol_enqueue_control_edge(control_id, &latch.binding,
                                                   false, one_shot);
  }
  if (one_shot) {
    host_interaction_protocol_consume_binding(
        control_id, latch.binding.binding_id, latch.binding.generation);
  }

  memset(&press_latches[index], 0, sizeof(press_latches[index]));
  return (latch.binding.flags & HOST_INTERACTION_BINDING_MIRROR) != 0;
}

bool host_interaction_pre_process_record(uint16_t keycode,
                                         keyrecord_t *record) {
  (void)keycode;

  if (pause_replay_bypass) {
    return true;
  }

  uint16_t control_id = 0;
  bool has_control = control_id_from_record(record, &control_id);
  if (!has_control || control_id != HOST_INTERACTION_PAUSE_CONTROL) {
    flush_pause_before_intervening_event();
  }

  if (!has_control) {
    return true;
  }
  if (control_id == HOST_INTERACTION_PAUSE_CONTROL) {
    return handle_pause_gesture(record);
  }
  return route_bound_control(control_id, record);
}

void host_interaction_on_session_reset(void) {
  switch (pause_gesture_state) {
  case PAUSE_GESTURE_FIRST_DOWN:
    replay_pause_event(true);
    pause_gesture_state = PAUSE_GESTURE_PASSTHROUGH_HELD;
    break;

  case PAUSE_GESTURE_WAIT_SECOND:
    replay_pause_tap();
    pause_gesture_state = PAUSE_GESTURE_IDLE;
    break;

  case PAUSE_GESTURE_SECOND_DOWN:
    replay_pause_tap();
    replay_pause_event(true);
    pause_gesture_state = PAUSE_GESTURE_PASSTHROUGH_HELD;
    break;

  case PAUSE_GESTURE_IDLE:
  case PAUSE_GESTURE_PASSTHROUGH_HELD:
    break;
  }
}

void host_interaction_housekeeping(void) {
  if (pause_gesture_state == PAUSE_GESTURE_FIRST_DOWN &&
      timer_elapsed32(pause_gesture_started_at) >=
          HOST_INTERACTION_DOUBLE_TAP_TERM_MS) {
    replay_pause_event(true);
    pause_gesture_state = PAUSE_GESTURE_PASSTHROUGH_HELD;
  } else if (pause_gesture_state == PAUSE_GESTURE_WAIT_SECOND &&
             timer_elapsed32(pause_gesture_started_at) >=
                 HOST_INTERACTION_DOUBLE_TAP_TERM_MS) {
    replay_pause_tap();
    pause_gesture_state = PAUSE_GESTURE_IDLE;
  }

  host_interaction_protocol_housekeeping();
}
