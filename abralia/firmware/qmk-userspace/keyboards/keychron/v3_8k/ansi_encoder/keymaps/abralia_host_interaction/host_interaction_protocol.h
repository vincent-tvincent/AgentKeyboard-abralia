// Copyright 2026 blue_lobster
// SPDX-License-Identifier: GPL-3.0-or-later

#pragma once

#include <stdbool.h>
#include <stdint.h>

#define HOST_INTERACTION_VALUE_ID 0x02
#define HOST_INTERACTION_PROTOCOL_VERSION 0x02
#define HOST_INTERACTION_EVENT_GROUP 0xF0

#define HOST_INTERACTION_DOUBLE_TAP_TERM_MS 300
#define HOST_INTERACTION_HEARTBEAT_TIMEOUT_MS 4000
#define HOST_INTERACTION_FORCE_MAX_LEASE_MS 30000
#define HOST_INTERACTION_MAX_TTL_MS 3600000UL
#define HOST_INTERACTION_EVENT_RETRY_MS 50
#define HOST_INTERACTION_EVENT_QUEUE_SIZE 32

enum host_interaction_control_kind {
  HOST_INTERACTION_CONTROL_KEY = 0,
  HOST_INTERACTION_CONTROL_ENCODER_CW = 1,
  HOST_INTERACTION_CONTROL_ENCODER_CCW = 2,
};

#define HOST_INTERACTION_CONTROL_ID(kind, primary, secondary)                  \
  ((uint16_t)((((uint16_t)(kind) & 0x03U) << 14) |                             \
              (((uint16_t)(primary) & 0x3FU) << 8) |                           \
              ((uint16_t)(secondary) & 0xFFU)))
#define HOST_INTERACTION_CONTROL_KIND(control_id)                              \
  ((uint8_t)(((control_id) >> 14) & 0x03U))
#define HOST_INTERACTION_CONTROL_PRIMARY(control_id)                           \
  ((uint8_t)(((control_id) >> 8) & 0x3FU))
#define HOST_INTERACTION_CONTROL_SECONDARY(control_id)                         \
  ((uint8_t)((control_id) & 0xFFU))

enum host_interaction_opcode {
  HOST_INTERACTION_GET_CAPABILITIES = 0x00,
  HOST_INTERACTION_CLAIM_SESSION = 0x01,
  HOST_INTERACTION_KEEPALIVE = 0x02,
  HOST_INTERACTION_RELEASE_SESSION = 0x03,

  HOST_INTERACTION_BEGIN_BINDING_REPLACE = 0x10,
  HOST_INTERACTION_WRITE_BINDINGS = 0x11,
  HOST_INTERACTION_COMMIT_BINDINGS = 0x12,
  HOST_INTERACTION_CLEAR_BINDINGS = 0x13,

  HOST_INTERACTION_BEGIN_FORCE_SCOPE = 0x20,
  HOST_INTERACTION_WRITE_FORCE_KEYS = 0x21,
  HOST_INTERACTION_COMMIT_FORCE_SCOPE = 0x22,
  HOST_INTERACTION_CLEAR_FORCE_SCOPE = 0x23,
  HOST_INTERACTION_GET_STATUS = 0x24,

  HOST_INTERACTION_ACK_EVENT = 0x30,
};

enum host_interaction_result {
  HOST_INTERACTION_RESULT_OK = 0x00,
  HOST_INTERACTION_RESULT_BUSY = 0x01,
  HOST_INTERACTION_RESULT_INVALID_PACKET = 0x02,
  HOST_INTERACTION_RESULT_UNSUPPORTED_VERSION = 0x03,
  HOST_INTERACTION_RESULT_STALE_SESSION = 0x04,
  HOST_INTERACTION_RESULT_STALE_GENERATION = 0x05,
  HOST_INTERACTION_RESULT_OUT_OF_RANGE = 0x06,
  HOST_INTERACTION_RESULT_UNBOUND = 0x07,
  HOST_INTERACTION_RESULT_QUEUE_OVERFLOW = 0x08,
  HOST_INTERACTION_RESULT_RESERVED_CONTROL = 0x09,
  HOST_INTERACTION_RESULT_INVALID_STATE = 0x0A,
};

enum host_interaction_binding_flags {
  HOST_INTERACTION_BINDING_MIRROR = 1 << 0,
  HOST_INTERACTION_BINDING_EVENT_DOWN = 1 << 1,
  HOST_INTERACTION_BINDING_EVENT_UP = 1 << 2,
};

#define HOST_INTERACTION_BINDING_ALLOWED_FLAGS                                 \
  (HOST_INTERACTION_BINDING_MIRROR | HOST_INTERACTION_BINDING_EVENT_DOWN |     \
   HOST_INTERACTION_BINDING_EVENT_UP)

enum host_interaction_lifetime {
  HOST_INTERACTION_LIFETIME_SESSION = 0x00,
  HOST_INTERACTION_LIFETIME_TTL = 0x01,
  HOST_INTERACTION_LIFETIME_ONE_SHOT = 0x02,
};

enum host_interaction_force_scope {
  HOST_INTERACTION_FORCE_ALL_CONFIGURED = 0x01,
  HOST_INTERACTION_FORCE_SELECTED = 0x02,
};

enum host_interaction_event_type {
  HOST_INTERACTION_EVENT_CONTROL_EDGE = 0x01,
  HOST_INTERACTION_EVENT_MODE_CHANGED = 0x02,
  HOST_INTERACTION_EVENT_QUEUE_OVERFLOW = 0x03,
  HOST_INTERACTION_EVENT_RGB_EFFECT_CHANGED = 0x04,
};

enum host_interaction_event_edge {
  HOST_INTERACTION_EDGE_UP = 0x00,
  HOST_INTERACTION_EDGE_DOWN = 0x01,
};

enum host_interaction_event_flags {
  HOST_INTERACTION_EVENT_MIRRORED = 1 << 0,
  HOST_INTERACTION_EVENT_CAPTURED = 1 << 1,
  HOST_INTERACTION_EVENT_ONE_SHOT_CONSUMED = 1 << 2,
  HOST_INTERACTION_EVENT_RETRANSMISSION = 1 << 3,
};

enum host_interaction_reset_reason {
  HOST_INTERACTION_RESET_NONE = 0x00,
  HOST_INTERACTION_RESET_SESSION_REPLACED = 0x01,
  HOST_INTERACTION_RESET_HEARTBEAT_TIMEOUT = 0x02,
  HOST_INTERACTION_RESET_EVENT_OVERFLOW = 0x03,
  HOST_INTERACTION_RESET_HOST_RELEASED = 0x04,
};

typedef struct {
  uint16_t binding_id;
  uint16_t generation;
  uint8_t flags;
  uint8_t lifetime;
} host_interaction_resolved_binding_t;

bool host_interaction_protocol_handle_via(uint8_t *data, uint8_t length);
void host_interaction_protocol_housekeeping(void);

bool host_interaction_protocol_session_alive(void);
bool host_interaction_protocol_rgb_effect25_selected(void);
void host_interaction_protocol_set_rgb_effect25_selected(bool selected);
bool host_interaction_protocol_resolve_binding(
    uint16_t control_id, host_interaction_resolved_binding_t *binding);
void host_interaction_protocol_consume_binding(uint16_t control_id,
                                               uint16_t binding_id,
                                               uint16_t generation);
bool host_interaction_protocol_enqueue_control_edge(
    uint16_t control_id, const host_interaction_resolved_binding_t *binding,
    bool pressed, bool one_shot_consumed);
void host_interaction_protocol_toggle_manual_mode(void);

void host_interaction_protocol_reset_hook(void);
