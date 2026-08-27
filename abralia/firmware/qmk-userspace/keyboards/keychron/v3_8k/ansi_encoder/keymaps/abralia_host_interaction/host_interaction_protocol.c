#include QMK_KEYBOARD_H

#include <string.h>

#include "host_interaction_protocol.h"
#include "raw_hid.h"
#include "timer.h"
#include "usb_descriptor.h"
#include "via.h"

#if defined(ENCODER_ENABLE)
#define HOST_INTERACTION_ENCODER_CONTROL_COUNT (NUM_ENCODERS * 2)
#else
#define HOST_INTERACTION_ENCODER_CONTROL_COUNT 0
#endif

#define HOST_INTERACTION_MATRIX_CONTROL_COUNT (MATRIX_ROWS * MATRIX_COLS)
#define HOST_INTERACTION_CONTROL_COUNT                                         \
  (HOST_INTERACTION_MATRIX_CONTROL_COUNT +                                     \
   HOST_INTERACTION_ENCODER_CONTROL_COUNT)
#define HOST_INTERACTION_PAUSE_CONTROL                                         \
  HOST_INTERACTION_CONTROL_ID(HOST_INTERACTION_CONTROL_KEY, 0, 16)

#define HOST_INTERACTION_STATUS_SESSION_VALID (1 << 0)
#define HOST_INTERACTION_STATUS_MANUAL_ACTIVE (1 << 1)
#define HOST_INTERACTION_STATUS_FORCE_ALL (1 << 2)
#define HOST_INTERACTION_STATUS_FORCE_SELECTED (1 << 3)
#define HOST_INTERACTION_STATUS_BINDING_STAGING (1 << 4)
#define HOST_INTERACTION_STATUS_FORCE_STAGING (1 << 5)
#define HOST_INTERACTION_STATUS_EVENT_OVERFLOW (1 << 6)

_Static_assert(RAW_EPSIZE == 32, "Host Interaction requires 32-byte Raw HID");
_Static_assert(MATRIX_ROWS > 0 && MATRIX_COLS > 16,
               "The V3 8K Pause position must be present");
_Static_assert(MATRIX_ROWS <= 64, "Control IDs allocate six bits to rows");
_Static_assert(MATRIX_COLS <= 256,
               "Control IDs allocate eight bits to columns");
#if defined(ENCODER_ENABLE)
_Static_assert(NUM_ENCODERS <= 64,
               "Control IDs allocate six bits to encoder indices");
#endif

typedef struct {
  uint16_t binding_id;
  uint32_t duration_ms;
  uint8_t flags;
  uint8_t lifetime;
} host_interaction_binding_t;

typedef struct {
  uint32_t activated_at;
  bool scope_active;
  bool consumed;
} host_interaction_binding_runtime_t;

typedef struct {
  uint8_t type;
  uint16_t sequence;
  uint16_t binding_generation;
  uint16_t binding_id;
  uint16_t control_id;
  uint8_t edge;
  uint8_t flags;
  uint32_t timestamp;
  bool sent;
  uint32_t last_sent_at;
} host_interaction_event_t;

static host_interaction_binding_t
    binding_tables[2][HOST_INTERACTION_CONTROL_COUNT];
static host_interaction_binding_runtime_t
    binding_runtime[HOST_INTERACTION_CONTROL_COUNT];
static uint8_t active_binding_table;
static uint8_t staging_binding_table = 1;
static uint16_t active_binding_generation;
static uint16_t staging_binding_generation;
static bool binding_staging_valid;

static bool force_mask[HOST_INTERACTION_CONTROL_COUNT];
static bool force_staging_mask[HOST_INTERACTION_CONTROL_COUNT];
static bool force_all;
static bool manual_global_mode;
static bool force_staging_valid;
static uint8_t force_staging_scope;
static uint16_t active_force_generation;
static uint16_t staging_force_generation;
static uint16_t force_staging_binding_generation;
static uint32_t force_staging_lease_ms;
static uint32_t force_started_at;
static uint32_t force_lease_ms;

static bool session_valid;
static uint32_t session_token;
static uint16_t heartbeat_sequence;
static uint32_t last_heartbeat_at;
static uint8_t last_reset_reason;
static bool event_overflowed;

static host_interaction_event_t event_queue[HOST_INTERACTION_EVENT_QUEUE_SIZE];
static uint8_t event_head;
static uint8_t event_tail;
static uint8_t event_count;
static uint16_t next_event_sequence = 1;
static uint16_t last_acked_event_sequence;

static uint16_t read_u16(const uint8_t *data) {
  return (uint16_t)data[0] | ((uint16_t)data[1] << 8);
}

static uint32_t read_u32(const uint8_t *data) {
  return (uint32_t)data[0] | ((uint32_t)data[1] << 8) |
         ((uint32_t)data[2] << 16) | ((uint32_t)data[3] << 24);
}

static void write_u16(uint8_t *data, uint16_t value) {
  data[0] = value & 0xFF;
  data[1] = value >> 8;
}

static void write_u32(uint8_t *data, uint32_t value) {
  data[0] = value & 0xFF;
  data[1] = (value >> 8) & 0xFF;
  data[2] = (value >> 16) & 0xFF;
  data[3] = value >> 24;
}

static bool generation_is_newer(uint16_t candidate, uint16_t current) {
  return candidate != current && (int16_t)(candidate - current) > 0;
}

static bool control_index(uint16_t control_id, uint16_t *index) {
  uint8_t kind = HOST_INTERACTION_CONTROL_KIND(control_id);
  uint8_t primary = HOST_INTERACTION_CONTROL_PRIMARY(control_id);
  uint8_t secondary = HOST_INTERACTION_CONTROL_SECONDARY(control_id);

  if (kind == HOST_INTERACTION_CONTROL_KEY) {
    if (primary >= MATRIX_ROWS || secondary >= MATRIX_COLS) {
      return false;
    }
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

static bool binding_exists(uint16_t index) {
  return binding_tables[active_binding_table][index].binding_id != 0;
}

static bool activation_scope_contains(uint16_t index) {
  return manual_global_mode || force_all || force_mask[index];
}

static bool any_forced_key(void) {
  for (uint16_t i = 0; i < HOST_INTERACTION_CONTROL_COUNT; i++) {
    if (force_mask[i]) {
      return true;
    }
  }
  return false;
}

static bool any_activation(void) {
  return manual_global_mode || force_all || any_forced_key();
}

static void recompute_binding_runtime(void) {
  uint32_t now = timer_read32();

  for (uint16_t i = 0; i < HOST_INTERACTION_CONTROL_COUNT; i++) {
    bool active =
        session_valid && binding_exists(i) && activation_scope_contains(i);

    if (active && !binding_runtime[i].scope_active) {
      binding_runtime[i].activated_at = now;
      binding_runtime[i].consumed = false;
    } else if (!active) {
      binding_runtime[i].consumed = false;
    }

    binding_runtime[i].scope_active = active;
  }
}

static void clear_force_internal(void) {
  memset(force_mask, 0, sizeof(force_mask));
  memset(force_staging_mask, 0, sizeof(force_staging_mask));
  force_all = false;
  force_staging_valid = false;
  force_staging_scope = 0;
  staging_force_generation = 0;
  force_staging_binding_generation = 0;
  force_staging_lease_ms = 0;
  force_started_at = 0;
  force_lease_ms = 0;
}

static void clear_activation_internal(void) {
  manual_global_mode = false;
  clear_force_internal();
  recompute_binding_runtime();
}

static void clear_event_queue(void) {
  memset(event_queue, 0, sizeof(event_queue));
  event_head = 0;
  event_tail = 0;
  event_count = 0;
  last_acked_event_sequence = 0;
}

static uint16_t allocate_event_sequence(void) {
  uint16_t sequence = next_event_sequence++;
  if (next_event_sequence == 0) {
    next_event_sequence = 1;
  }
  return sequence;
}

static bool enqueue_event_internal(uint8_t type, uint16_t binding_generation,
                                   uint16_t binding_id, uint16_t control_id,
                                   uint8_t edge, uint8_t flags) {
  if (!session_valid || event_count >= HOST_INTERACTION_EVENT_QUEUE_SIZE) {
    return false;
  }

  host_interaction_event_t *event = &event_queue[event_tail];
  *event = (host_interaction_event_t){
      .type = type,
      .sequence = allocate_event_sequence(),
      .binding_generation = binding_generation,
      .binding_id = binding_id,
      .control_id = control_id,
      .edge = edge,
      .flags = flags,
      .timestamp = timer_read32(),
  };

  event_tail = (event_tail + 1) % HOST_INTERACTION_EVENT_QUEUE_SIZE;
  event_count++;
  return true;
}

static void handle_event_overflow(void) {
  clear_activation_internal();
  clear_event_queue();
  event_overflowed = true;
  last_reset_reason = HOST_INTERACTION_RESET_EVENT_OVERFLOW;
  enqueue_event_internal(HOST_INTERACTION_EVENT_QUEUE_OVERFLOW,
                         active_binding_generation, 0, 0, 0, 0);
}

static void send_head_event(void) {
  if (!session_valid || event_count == 0) {
    return;
  }

  host_interaction_event_t *event = &event_queue[event_head];
  uint32_t now = timer_read32();
  if (event->sent &&
      timer_elapsed32(event->last_sent_at) < HOST_INTERACTION_EVENT_RETRY_MS) {
    return;
  }

  uint8_t report[RAW_EPSIZE] = {0};
  report[0] = HOST_INTERACTION_EVENT_GROUP;
  report[1] = id_custom_channel;
  report[2] = HOST_INTERACTION_VALUE_ID;
  report[3] = HOST_INTERACTION_PROTOCOL_VERSION;
  report[4] = event->type;
  write_u32(&report[5], session_token);
  write_u16(&report[9], event->sequence);
  write_u16(&report[11], event->binding_generation);
  write_u16(&report[13], event->binding_id);
  write_u16(&report[15], event->control_id);
  report[17] = event->edge;
  report[18] =
      event->flags | (event->sent ? HOST_INTERACTION_EVENT_RETRANSMISSION : 0);
  write_u32(&report[19], event->timestamp);

  raw_hid_send(report, RAW_EPSIZE);
  event->sent = true;
  event->last_sent_at = now;
}

__attribute__((weak)) void host_interaction_protocol_reset_hook(void) {}

static void reset_host_session(uint8_t reason) {
  host_interaction_protocol_reset_hook();

  session_valid = false;
  session_token = 0;
  heartbeat_sequence = 0;
  last_heartbeat_at = 0;
  manual_global_mode = false;
  clear_force_internal();
  memset(binding_tables, 0, sizeof(binding_tables));
  memset(binding_runtime, 0, sizeof(binding_runtime));
  active_binding_table = 0;
  staging_binding_table = 1;
  active_binding_generation = 0;
  staging_binding_generation = 0;
  binding_staging_valid = false;
  clear_event_queue();
  next_event_sequence = 1;
  active_force_generation = 0;
  event_overflowed = false;
  last_reset_reason = reason;
}

static uint8_t validate_session(uint32_t token) {
  if (!session_valid || token == 0 || token != session_token) {
    return HOST_INTERACTION_RESULT_STALE_SESSION;
  }
  return HOST_INTERACTION_RESULT_OK;
}

static uint8_t binding_count(void) {
  uint8_t count = 0;
  for (uint16_t i = 0; i < HOST_INTERACTION_CONTROL_COUNT; i++) {
    if (binding_exists(i)) {
      count++;
    }
  }
  return count;
}

static uint8_t forced_key_count(void) {
  uint8_t count = 0;
  for (uint16_t i = 0; i < HOST_INTERACTION_CONTROL_COUNT; i++) {
    if (force_mask[i]) {
      count++;
    }
  }
  return count;
}

static uint8_t status_flags(void) {
  uint8_t flags = 0;
  if (session_valid) {
    flags |= HOST_INTERACTION_STATUS_SESSION_VALID;
  }
  if (manual_global_mode) {
    flags |= HOST_INTERACTION_STATUS_MANUAL_ACTIVE;
  }
  if (force_all) {
    flags |= HOST_INTERACTION_STATUS_FORCE_ALL;
  }
  if (any_forced_key()) {
    flags |= HOST_INTERACTION_STATUS_FORCE_SELECTED;
  }
  if (binding_staging_valid) {
    flags |= HOST_INTERACTION_STATUS_BINDING_STAGING;
  }
  if (force_staging_valid) {
    flags |= HOST_INTERACTION_STATUS_FORCE_STAGING;
  }
  if (event_overflowed) {
    flags |= HOST_INTERACTION_STATUS_EVENT_OVERFLOW;
  }
  return flags;
}

static void write_common_response(uint8_t *data, uint8_t opcode,
                                  uint8_t result) {
  memset(&data[5], 0, RAW_EPSIZE - 5);
  data[0] = data[0] == id_custom_get_value ? id_custom_get_value
                                           : id_custom_set_value;
  data[1] = id_custom_channel;
  data[2] = HOST_INTERACTION_VALUE_ID;
  data[3] = HOST_INTERACTION_PROTOCOL_VERSION;
  data[4] = opcode;
  data[5] = result;
  write_u32(&data[6], session_token);
  write_u16(&data[10], active_binding_generation);
  data[12] = status_flags();
  data[13] = binding_count();
  data[14] = forced_key_count();
  data[15] = event_count;
  data[16] = last_reset_reason;
  write_u16(&data[17], active_force_generation);
  write_u16(&data[19], heartbeat_sequence);
}

static void write_capabilities(uint8_t *data) {
  data[12] = MATRIX_ROWS;
  data[13] = MATRIX_COLS;
#if defined(ENCODER_ENABLE)
  data[14] = NUM_ENCODERS;
#else
  data[14] = 0;
#endif
  data[15] = HOST_INTERACTION_EVENT_QUEUE_SIZE;
  write_u16(&data[16], HOST_INTERACTION_CONTROL_COUNT);
  write_u16(&data[18], HOST_INTERACTION_DOUBLE_TAP_TERM_MS);
  write_u16(&data[20], HOST_INTERACTION_HEARTBEAT_TIMEOUT_MS);
  write_u16(&data[22], HOST_INTERACTION_FORCE_MAX_LEASE_MS);
  data[24] = HOST_INTERACTION_BINDING_ALLOWED_FLAGS;
  data[25] = (1 << HOST_INTERACTION_LIFETIME_SESSION) |
             (1 << HOST_INTERACTION_LIFETIME_TTL) |
             (1 << HOST_INTERACTION_LIFETIME_ONE_SHOT);
}

static uint8_t claim_session(uint32_t token) {
  if (token == 0) {
    return HOST_INTERACTION_RESULT_INVALID_PACKET;
  }

  if (session_valid && token == session_token) {
    last_heartbeat_at = timer_read32();
    return HOST_INTERACTION_RESULT_OK;
  }

  reset_host_session(HOST_INTERACTION_RESET_SESSION_REPLACED);
  session_valid = true;
  session_token = token;
  last_heartbeat_at = timer_read32();
  last_reset_reason = HOST_INTERACTION_RESET_NONE;
  return HOST_INTERACTION_RESULT_OK;
}

static uint8_t keepalive(uint32_t token, uint16_t sequence) {
  uint8_t result = validate_session(token);
  if (result != HOST_INTERACTION_RESULT_OK) {
    return result;
  }

  heartbeat_sequence = sequence;
  last_heartbeat_at = timer_read32();
  return HOST_INTERACTION_RESULT_OK;
}

static uint8_t begin_binding_replace(uint32_t token, uint16_t generation) {
  uint8_t result = validate_session(token);
  if (result != HOST_INTERACTION_RESULT_OK) {
    return result;
  }
  if (generation == 0 ||
      !generation_is_newer(generation, active_binding_generation)) {
    return HOST_INTERACTION_RESULT_STALE_GENERATION;
  }
  if (binding_staging_valid) {
    return generation == staging_binding_generation
               ? HOST_INTERACTION_RESULT_OK
               : HOST_INTERACTION_RESULT_BUSY;
  }

  memset(binding_tables[staging_binding_table], 0,
         sizeof(binding_tables[staging_binding_table]));
  staging_binding_generation = generation;
  binding_staging_valid = true;
  return HOST_INTERACTION_RESULT_OK;
}

static uint8_t validate_lifetime(uint8_t lifetime, uint32_t duration_ms) {
  if (lifetime == HOST_INTERACTION_LIFETIME_SESSION) {
    return duration_ms == 0 ? HOST_INTERACTION_RESULT_OK
                            : HOST_INTERACTION_RESULT_INVALID_PACKET;
  }
  if (lifetime != HOST_INTERACTION_LIFETIME_TTL &&
      lifetime != HOST_INTERACTION_LIFETIME_ONE_SHOT) {
    return HOST_INTERACTION_RESULT_INVALID_PACKET;
  }
  if (duration_ms == 0 || duration_ms > HOST_INTERACTION_MAX_TTL_MS) {
    return HOST_INTERACTION_RESULT_OUT_OF_RANGE;
  }
  return HOST_INTERACTION_RESULT_OK;
}

static uint8_t write_bindings(const uint8_t *data, uint8_t length,
                              uint32_t token) {
  uint8_t result = validate_session(token);
  if (result != HOST_INTERACTION_RESULT_OK) {
    return result;
  }
  if (length < RAW_EPSIZE) {
    return HOST_INTERACTION_RESULT_INVALID_PACKET;
  }

  uint16_t generation = read_u16(&data[9]);
  uint8_t flags = data[11];
  uint8_t lifetime = data[12];
  uint32_t duration_ms = read_u32(&data[13]);
  uint8_t count = data[17];

  if (!binding_staging_valid || generation != staging_binding_generation) {
    return HOST_INTERACTION_RESULT_STALE_GENERATION;
  }
  if ((flags & ~HOST_INTERACTION_BINDING_ALLOWED_FLAGS) != 0 ||
      (flags & (HOST_INTERACTION_BINDING_EVENT_DOWN |
                HOST_INTERACTION_BINDING_EVENT_UP)) == 0 ||
      count == 0 || count > 3) {
    return HOST_INTERACTION_RESULT_INVALID_PACKET;
  }
  result = validate_lifetime(lifetime, duration_ms);
  if (result != HOST_INTERACTION_RESULT_OK) {
    return result;
  }

  for (uint8_t i = 0; i < count; i++) {
    uint8_t offset = 18 + i * 4;
    uint16_t control_id = read_u16(&data[offset]);
    uint16_t binding_id = read_u16(&data[offset + 2]);
    uint16_t index;

    if (!control_index(control_id, &index)) {
      return HOST_INTERACTION_RESULT_OUT_OF_RANGE;
    }
    if (control_id == HOST_INTERACTION_PAUSE_CONTROL) {
      return HOST_INTERACTION_RESULT_RESERVED_CONTROL;
    }
    if (binding_id == 0) {
      return HOST_INTERACTION_RESULT_INVALID_PACKET;
    }
  }

  for (uint8_t i = 0; i < count; i++) {
    uint8_t offset = 18 + i * 4;
    uint16_t control_id = read_u16(&data[offset]);
    uint16_t binding_id = read_u16(&data[offset + 2]);
    uint16_t index;
    control_index(control_id, &index);
    binding_tables[staging_binding_table][index] = (host_interaction_binding_t){
        .binding_id = binding_id,
        .duration_ms = duration_ms,
        .flags = flags,
        .lifetime = lifetime,
    };
  }

  return HOST_INTERACTION_RESULT_OK;
}

static uint8_t commit_bindings(uint32_t token, uint16_t generation) {
  uint8_t result = validate_session(token);
  if (result != HOST_INTERACTION_RESULT_OK) {
    return result;
  }
  if (!binding_staging_valid || generation != staging_binding_generation) {
    return HOST_INTERACTION_RESULT_STALE_GENERATION;
  }

  uint8_t previous_active = active_binding_table;
  active_binding_table = staging_binding_table;
  staging_binding_table = previous_active;
  active_binding_generation = staging_binding_generation;
  binding_staging_valid = false;
  memset(binding_runtime, 0, sizeof(binding_runtime));
  clear_force_internal();
  recompute_binding_runtime();
  return HOST_INTERACTION_RESULT_OK;
}

static uint8_t clear_bindings(uint32_t token, uint16_t generation) {
  uint8_t result = validate_session(token);
  if (result != HOST_INTERACTION_RESULT_OK) {
    return result;
  }
  if (generation == 0 ||
      !generation_is_newer(generation, active_binding_generation)) {
    return HOST_INTERACTION_RESULT_STALE_GENERATION;
  }

  memset(binding_tables, 0, sizeof(binding_tables));
  memset(binding_runtime, 0, sizeof(binding_runtime));
  active_binding_generation = generation;
  binding_staging_valid = false;
  clear_force_internal();
  return HOST_INTERACTION_RESULT_OK;
}

static uint8_t begin_force_scope(const uint8_t *data, uint32_t token) {
  uint8_t result = validate_session(token);
  if (result != HOST_INTERACTION_RESULT_OK) {
    return result;
  }

  uint16_t binding_generation = read_u16(&data[9]);
  uint16_t force_generation = read_u16(&data[11]);
  uint8_t scope = data[13];
  uint32_t lease_ms = read_u32(&data[14]);

  if (binding_generation != active_binding_generation) {
    return HOST_INTERACTION_RESULT_STALE_GENERATION;
  }
  if (force_generation == 0 ||
      !generation_is_newer(force_generation, active_force_generation)) {
    return HOST_INTERACTION_RESULT_STALE_GENERATION;
  }
  if (scope != HOST_INTERACTION_FORCE_ALL_CONFIGURED &&
      scope != HOST_INTERACTION_FORCE_SELECTED) {
    return HOST_INTERACTION_RESULT_INVALID_PACKET;
  }
  if (lease_ms == 0 || lease_ms > HOST_INTERACTION_FORCE_MAX_LEASE_MS) {
    return HOST_INTERACTION_RESULT_OUT_OF_RANGE;
  }
  if (force_staging_valid) {
    return force_generation == staging_force_generation
               ? HOST_INTERACTION_RESULT_OK
               : HOST_INTERACTION_RESULT_BUSY;
  }

  memset(force_staging_mask, 0, sizeof(force_staging_mask));
  force_staging_valid = true;
  force_staging_scope = scope;
  staging_force_generation = force_generation;
  force_staging_binding_generation = binding_generation;
  force_staging_lease_ms = lease_ms;
  return HOST_INTERACTION_RESULT_OK;
}

static uint8_t write_force_keys(const uint8_t *data, uint32_t token) {
  uint8_t result = validate_session(token);
  if (result != HOST_INTERACTION_RESULT_OK) {
    return result;
  }

  uint16_t generation = read_u16(&data[9]);
  uint8_t count = data[11];
  if (!force_staging_valid || generation != staging_force_generation) {
    return HOST_INTERACTION_RESULT_STALE_GENERATION;
  }
  if (force_staging_scope != HOST_INTERACTION_FORCE_SELECTED || count == 0 ||
      count > 10) {
    return HOST_INTERACTION_RESULT_INVALID_PACKET;
  }

  for (uint8_t i = 0; i < count; i++) {
    uint16_t control_id = read_u16(&data[12 + i * 2]);
    uint16_t index;
    if (!control_index(control_id, &index)) {
      return HOST_INTERACTION_RESULT_OUT_OF_RANGE;
    }
    if (control_id == HOST_INTERACTION_PAUSE_CONTROL) {
      return HOST_INTERACTION_RESULT_RESERVED_CONTROL;
    }
    if (!binding_exists(index)) {
      return HOST_INTERACTION_RESULT_UNBOUND;
    }
  }

  for (uint8_t i = 0; i < count; i++) {
    uint16_t index;
    control_index(read_u16(&data[12 + i * 2]), &index);
    force_staging_mask[index] = true;
  }
  return HOST_INTERACTION_RESULT_OK;
}

static uint8_t commit_force_scope(uint32_t token, uint16_t generation) {
  uint8_t result = validate_session(token);
  if (result != HOST_INTERACTION_RESULT_OK) {
    return result;
  }
  if (!force_staging_valid || generation != staging_force_generation ||
      force_staging_binding_generation != active_binding_generation) {
    return HOST_INTERACTION_RESULT_STALE_GENERATION;
  }

  if (force_staging_scope == HOST_INTERACTION_FORCE_SELECTED) {
    bool any = false;
    for (uint16_t i = 0; i < HOST_INTERACTION_CONTROL_COUNT; i++) {
      if (force_staging_mask[i]) {
        any = true;
        if (!binding_exists(i)) {
          return HOST_INTERACTION_RESULT_UNBOUND;
        }
      }
    }
    if (!any) {
      return HOST_INTERACTION_RESULT_INVALID_PACKET;
    }
  }

  memcpy(force_mask, force_staging_mask, sizeof(force_mask));
  force_all = force_staging_scope == HOST_INTERACTION_FORCE_ALL_CONFIGURED;
  active_force_generation = staging_force_generation;
  force_lease_ms = force_staging_lease_ms;
  force_started_at = timer_read32();
  force_staging_valid = false;
  recompute_binding_runtime();
  return HOST_INTERACTION_RESULT_OK;
}

static uint8_t clear_force_scope(uint32_t token) {
  uint8_t result = validate_session(token);
  if (result != HOST_INTERACTION_RESULT_OK) {
    return result;
  }
  clear_force_internal();
  recompute_binding_runtime();
  return HOST_INTERACTION_RESULT_OK;
}

static uint8_t ack_event(uint32_t token, uint16_t sequence) {
  uint8_t result = validate_session(token);
  if (result != HOST_INTERACTION_RESULT_OK) {
    return result;
  }
  if (sequence == last_acked_event_sequence) {
    return HOST_INTERACTION_RESULT_OK;
  }
  if (event_count == 0 || event_queue[event_head].sequence != sequence) {
    return HOST_INTERACTION_RESULT_INVALID_STATE;
  }

  last_acked_event_sequence = sequence;
  memset(&event_queue[event_head], 0, sizeof(event_queue[event_head]));
  event_head = (event_head + 1) % HOST_INTERACTION_EVENT_QUEUE_SIZE;
  event_count--;
  return HOST_INTERACTION_RESULT_OK;
}

bool host_interaction_protocol_handle_via(uint8_t *data, uint8_t length) {
  if (length != RAW_EPSIZE || data[1] != id_custom_channel ||
      data[2] != HOST_INTERACTION_VALUE_ID) {
    return false;
  }

  uint8_t verb = data[0];
  uint8_t version = data[3];
  uint8_t opcode = data[4];
  uint32_t token = read_u32(&data[5]);
  uint8_t result = HOST_INTERACTION_RESULT_INVALID_PACKET;

  if (version != HOST_INTERACTION_PROTOCOL_VERSION) {
    write_common_response(data, opcode,
                          HOST_INTERACTION_RESULT_UNSUPPORTED_VERSION);
    return true;
  }

  if (verb == id_custom_get_value) {
    if (opcode == HOST_INTERACTION_GET_CAPABILITIES) {
      write_common_response(data, opcode, HOST_INTERACTION_RESULT_OK);
      write_capabilities(data);
      return true;
    }
    if (opcode == HOST_INTERACTION_GET_STATUS) {
      write_common_response(data, opcode, HOST_INTERACTION_RESULT_OK);
      return true;
    }
    write_common_response(data, opcode, HOST_INTERACTION_RESULT_INVALID_PACKET);
    return true;
  }

  if (verb != id_custom_set_value) {
    write_common_response(data, opcode, HOST_INTERACTION_RESULT_INVALID_PACKET);
    return true;
  }

  switch (opcode) {
  case HOST_INTERACTION_CLAIM_SESSION:
    result = claim_session(token);
    break;
  case HOST_INTERACTION_KEEPALIVE:
    result = keepalive(token, read_u16(&data[9]));
    break;
  case HOST_INTERACTION_RELEASE_SESSION:
    result = validate_session(token);
    if (result == HOST_INTERACTION_RESULT_OK) {
      reset_host_session(HOST_INTERACTION_RESET_HOST_RELEASED);
    }
    break;
  case HOST_INTERACTION_BEGIN_BINDING_REPLACE:
    result = begin_binding_replace(token, read_u16(&data[9]));
    break;
  case HOST_INTERACTION_WRITE_BINDINGS:
    result = write_bindings(data, length, token);
    break;
  case HOST_INTERACTION_COMMIT_BINDINGS:
    result = commit_bindings(token, read_u16(&data[9]));
    break;
  case HOST_INTERACTION_CLEAR_BINDINGS:
    result = clear_bindings(token, read_u16(&data[9]));
    break;
  case HOST_INTERACTION_BEGIN_FORCE_SCOPE:
    result = begin_force_scope(data, token);
    break;
  case HOST_INTERACTION_WRITE_FORCE_KEYS:
    result = write_force_keys(data, token);
    break;
  case HOST_INTERACTION_COMMIT_FORCE_SCOPE:
    result = commit_force_scope(token, read_u16(&data[9]));
    break;
  case HOST_INTERACTION_CLEAR_FORCE_SCOPE:
    result = clear_force_scope(token);
    break;
  case HOST_INTERACTION_ACK_EVENT:
    result = ack_event(token, read_u16(&data[9]));
    break;
  default:
    result = HOST_INTERACTION_RESULT_INVALID_PACKET;
    break;
  }

  write_common_response(data, opcode, result);
  return true;
}

bool host_interaction_protocol_session_alive(void) { return session_valid; }

bool host_interaction_protocol_resolve_binding(
    uint16_t control_id, host_interaction_resolved_binding_t *binding) {
  uint16_t index;
  if (!session_valid || !control_index(control_id, &index) ||
      !binding_runtime[index].scope_active || binding_runtime[index].consumed) {
    return false;
  }

  host_interaction_binding_t *configured =
      &binding_tables[active_binding_table][index];
  if (configured->binding_id == 0) {
    return false;
  }

  if (configured->lifetime == HOST_INTERACTION_LIFETIME_TTL &&
      timer_elapsed32(binding_runtime[index].activated_at) >=
          configured->duration_ms) {
    binding_runtime[index].consumed = true;
    return false;
  }

  *binding = (host_interaction_resolved_binding_t){
      .binding_id = configured->binding_id,
      .generation = active_binding_generation,
      .flags = configured->flags,
      .lifetime = configured->lifetime,
  };
  return true;
}

void host_interaction_protocol_consume_binding(uint16_t control_id,
                                               uint16_t binding_id,
                                               uint16_t generation) {
  uint16_t index;
  if (!control_index(control_id, &index) ||
      generation != active_binding_generation) {
    return;
  }

  host_interaction_binding_t *configured =
      &binding_tables[active_binding_table][index];
  if (configured->binding_id == binding_id &&
      configured->lifetime == HOST_INTERACTION_LIFETIME_ONE_SHOT) {
    binding_runtime[index].consumed = true;
  }
}

bool host_interaction_protocol_enqueue_control_edge(
    uint16_t control_id, const host_interaction_resolved_binding_t *binding,
    bool pressed, bool one_shot_consumed) {
  uint8_t flags = binding->flags & HOST_INTERACTION_BINDING_MIRROR
                      ? HOST_INTERACTION_EVENT_MIRRORED
                      : HOST_INTERACTION_EVENT_CAPTURED;
  if (one_shot_consumed) {
    flags |= HOST_INTERACTION_EVENT_ONE_SHOT_CONSUMED;
  }

  if (!enqueue_event_internal(
          HOST_INTERACTION_EVENT_CONTROL_EDGE, binding->generation,
          binding->binding_id, control_id,
          pressed ? HOST_INTERACTION_EDGE_DOWN : HOST_INTERACTION_EDGE_UP,
          flags)) {
    handle_event_overflow();
    return false;
  }
  return true;
}

void host_interaction_protocol_toggle_manual_mode(void) {
  if (!session_valid) {
    return;
  }

  if (any_activation()) {
    clear_activation_internal();
  } else {
    manual_global_mode = true;
    recompute_binding_runtime();
  }

  if (!enqueue_event_internal(
          HOST_INTERACTION_EVENT_MODE_CHANGED, active_binding_generation, 0,
          HOST_INTERACTION_PAUSE_CONTROL, any_activation() ? 1 : 0, 0)) {
    handle_event_overflow();
  }
}

void host_interaction_protocol_housekeeping(void) {
  if (session_valid && timer_elapsed32(last_heartbeat_at) >=
                           HOST_INTERACTION_HEARTBEAT_TIMEOUT_MS) {
    reset_host_session(HOST_INTERACTION_RESET_HEARTBEAT_TIMEOUT);
    return;
  }

  if ((force_all || any_forced_key()) && force_lease_ms > 0 &&
      timer_elapsed32(force_started_at) >= force_lease_ms) {
    clear_force_internal();
    recompute_binding_runtime();
  }

  for (uint16_t i = 0; i < HOST_INTERACTION_CONTROL_COUNT; i++) {
    if (!binding_runtime[i].scope_active || binding_runtime[i].consumed) {
      continue;
    }
    host_interaction_binding_t *binding =
        &binding_tables[active_binding_table][i];
    if (binding->lifetime == HOST_INTERACTION_LIFETIME_TTL &&
        timer_elapsed32(binding_runtime[i].activated_at) >=
            binding->duration_ms) {
      binding_runtime[i].consumed = true;
    }
  }

  send_head_event();
}
