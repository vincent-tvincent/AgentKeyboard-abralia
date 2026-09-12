// Copyright 2026 blue_lobster
// SPDX-License-Identifier: GPL-3.0-or-later

#include "qmk_test.h"
#include "host_interaction.h"
#include "host_interaction_protocol.h"

#include <assert.h>
#include <stdio.h>
#include <string.h>

static uint32_t now;
static uint8_t packet[RAW_EPSIZE], event_report[RAW_EPSIZE];
static unsigned sent, replayed;
static keyevent_t replay[8];

uint32_t timer_read32(void) { return now; }
uint32_t timer_elapsed32(uint32_t started) { return now - started; }
void raw_hid_send(uint8_t *report, uint8_t length) {
    assert(length == RAW_EPSIZE);
    memcpy(event_report, report, length);
    sent++;
}
void action_exec(keyevent_t event) {
    assert(replayed < 8);
    replay[replayed++] = event;
    keyrecord_t record = {.event = event};
    assert(host_interaction_pre_process_record(0, &record));
}
void host_interaction_protocol_reset_hook(void) {
    host_interaction_on_session_reset();
}
static uint16_t read16(const uint8_t *data) {
    return data[0] | ((uint16_t)data[1] << 8);
}
static void put16(uint8_t *data, uint16_t value) {
    data[0] = value;
    data[1] = value >> 8;
}
static void request(uint8_t opcode, bool get) {
    memset(packet, 0, sizeof(packet));
    packet[0] = get ? id_custom_get_value : id_custom_set_value;
    packet[2] = HOST_INTERACTION_VALUE_ID;
    packet[3] = HOST_INTERACTION_PROTOCOL_VERSION;
    packet[4] = opcode;
    packet[5] = 1; // Session token.
}
static void submit(uint8_t result) {
    assert(host_interaction_protocol_handle_via(packet, sizeof(packet)));
    assert(packet[3] == HOST_INTERACTION_PROTOCOL_VERSION);
    assert(packet[5] == result);
}
static void generation_request(uint8_t opcode) {
    request(opcode, false);
    put16(&packet[9], 1);
    submit(HOST_INTERACTION_RESULT_OK);
}
static void bind(uint16_t control, uint16_t id, uint8_t result) {
    request(HOST_INTERACTION_WRITE_BINDINGS, false);
    put16(&packet[9], 1);
    packet[11] = HOST_INTERACTION_BINDING_EVENT_DOWN | HOST_INTERACTION_BINDING_EVENT_UP;
    packet[12] = HOST_INTERACTION_LIFETIME_SESSION;
    packet[17] = 1;
    put16(&packet[18], control);
    put16(&packet[20], id);
    submit(result);
}
static bool key(uint8_t row, uint8_t col, bool pressed) {
    keyrecord_t record = {.event = MAKE_KEYEVENT(row, col, pressed)};
    return host_interaction_pre_process_record(0, &record);
}
static bool toggle(bool pressed) {
    return key(HOST_INTERACTION_PAUSE_ROW, HOST_INTERACTION_PAUSE_COL, pressed);
}
static void double_tap(void) {
    assert(!toggle(true));
    assert(!toggle(false));
    now += 50;
    assert(!toggle(true));
    assert(!toggle(false));
}
static void expect_event(uint8_t type, uint8_t edge, uint16_t control) {
    unsigned previous = sent;
    host_interaction_protocol_housekeeping();
    assert(sent == previous + 1);
    assert(event_report[0] == HOST_INTERACTION_EVENT_GROUP);
    assert(event_report[3] == HOST_INTERACTION_PROTOCOL_VERSION);
    assert(event_report[4] == type);
    assert(event_report[17] == edge);
    assert(read16(&event_report[15]) == control);
    uint16_t sequence = read16(&event_report[9]);
    now += HOST_INTERACTION_EVENT_RETRY_MS;
    host_interaction_protocol_housekeeping();
    assert(sent == previous + 2);
    assert(read16(&event_report[9]) == sequence);
    assert(event_report[18] & HOST_INTERACTION_EVENT_RETRANSMISSION);
    request(HOST_INTERACTION_ACK_EVENT, false);
    put16(&packet[9], sequence);
    submit(HOST_INTERACTION_RESULT_OK);
}

static void bind_tap(uint16_t generation, uint16_t id) {
    request(HOST_INTERACTION_BEGIN_BINDING_REPLACE, false);
    put16(&packet[9], generation);
    submit(HOST_INTERACTION_RESULT_OK);
    request(HOST_INTERACTION_WRITE_BINDINGS, false);
    put16(&packet[9], generation);
    packet[11] = HOST_INTERACTION_BINDING_EVENT_UP;
    packet[12] = HOST_INTERACTION_LIFETIME_SESSION;
    packet[17] = 1;
    put16(&packet[18], HOST_INTERACTION_PAUSE_CONTROL);
    put16(&packet[20], id);
    submit(HOST_INTERACTION_RESULT_OK);
    request(HOST_INTERACTION_COMMIT_BINDINGS, false);
    put16(&packet[9], generation);
    submit(HOST_INTERACTION_RESULT_OK);
}

static void setup_tap(bool active) {
    if (host_interaction_protocol_session_alive()) {
        request(HOST_INTERACTION_RELEASE_SESSION, false);
        submit(HOST_INTERACTION_RESULT_OK);
    }
    host_interaction_on_rgb_effect_changed(true);
    request(HOST_INTERACTION_CLAIM_SESSION, false);
    submit(HOST_INTERACTION_RESULT_OK);
    bind_tap(1, 91);
    replayed = 0;
    if (active) {
        double_tap();
        expect_event(HOST_INTERACTION_EVENT_MODE_CHANGED, 1, HOST_INTERACTION_PAUSE_CONTROL);
    }
}

static void expect_no_events(void) {
    request(HOST_INTERACTION_GET_STATUS, true);
    submit(HOST_INTERACTION_RESULT_OK);
    assert(packet[15] == 0);
}

static void expect_tap(void) {
    expect_event(HOST_INTERACTION_EVENT_CONTROL_EDGE, HOST_INTERACTION_EDGE_UP, HOST_INTERACTION_PAUSE_CONTROL);
    assert(read16(&event_report[13]) == 91);
    assert(event_report[18] & HOST_INTERACTION_EVENT_CAPTURED);
    assert(replayed == 0);
    expect_no_events();
}

static void single_tap_capture_tests(void) {
    setup_tap(true);
    assert(!toggle(true) && !toggle(false));
    now += HOST_INTERACTION_DOUBLE_TAP_TERM_MS - 1;
    host_interaction_housekeeping();
    expect_no_events();
    assert(replayed == 0);
    now++;
    host_interaction_housekeeping();
    expect_tap();
    assert(key(0, 14, true) && key(0, 14, false)); // Print Screen is still ordinary.

    double_tap(); // A bound double tap exits without single-tap delivery.
    expect_event(HOST_INTERACTION_EVENT_MODE_CHANGED, 0, HOST_INTERACTION_PAUSE_CONTROL);
    expect_no_events();
    assert(replayed == 0);
    assert(!toggle(true) && !toggle(false)); // Inactive single tap is ordinary.
    now += HOST_INTERACTION_DOUBLE_TAP_TERM_MS;
    host_interaction_housekeeping();
    assert(replayed == 2);

    // Below the deadline is double tap; exactly at it is two captured singles.
    setup_tap(true);
    assert(!toggle(true) && !toggle(false));
    now += HOST_INTERACTION_DOUBLE_TAP_TERM_MS - 1;
    assert(!toggle(true) && !toggle(false));
    expect_event(HOST_INTERACTION_EVENT_MODE_CHANGED, 0, HOST_INTERACTION_PAUSE_CONTROL);
    expect_no_events();
    assert(replayed == 0);
    setup_tap(true);
    assert(!toggle(true) && !toggle(false));
    now += HOST_INTERACTION_DOUBLE_TAP_TERM_MS;
    assert(!toggle(true) && !toggle(false));
    expect_tap();
    now += HOST_INTERACTION_DOUBLE_TAP_TERM_MS;
    host_interaction_housekeeping();
    expect_tap();

    // A new table generation cannot acquire a tap already in flight.
    setup_tap(true);
    assert(!toggle(true) && !toggle(false));
    bind_tap(2, 91);
    now += HOST_INTERACTION_DOUBLE_TAP_TERM_MS;
    host_interaction_housekeeping();
    expect_no_events();
    assert(replayed == 0);

    // Held captures never leak a DOWN/UP to ordinary QMK input.
    setup_tap(true);
    assert(!toggle(true));
    now += HOST_INTERACTION_DOUBLE_TAP_TERM_MS;
    host_interaction_housekeeping();
    assert(replayed == 0);
    now += 500;
    assert(!toggle(false));
    expect_tap();
    setup_tap(true);
    assert(!toggle(true));
    now += HOST_INTERACTION_DOUBLE_TAP_TERM_MS; // No housekeeping before UP.
    assert(!toggle(false));
    expect_tap();

    // Intervening input resolves the gesture, preserving suppression when held.
    setup_tap(true);
    assert(!toggle(true));
    assert(key(4, 0, true) && key(4, 0, false));
    assert(!toggle(false));
    expect_tap();
    setup_tap(true);
    assert(!toggle(true) && !toggle(false));
    assert(key(4, 0, true) && key(4, 0, false));
    expect_tap();

    // Effect loss cancels the deferred callback.
    setup_tap(true);
    assert(!toggle(true) && !toggle(false));
    host_interaction_on_rgb_effect_changed(false);
    expect_event(HOST_INTERACTION_EVENT_RGB_EFFECT_CHANGED, 0, 0);
    expect_event(HOST_INTERACTION_EVENT_MODE_CHANGED, 0, HOST_INTERACTION_PAUSE_CONTROL);
    now += HOST_INTERACTION_DOUBLE_TAP_TERM_MS;
    host_interaction_housekeeping();
    expect_no_events();
    assert(replayed == 0);

    // Reset during FIRST_DOWN, CAPTURED_HELD or SECOND_DOWN swallows matching UP.
    for (unsigned phase = 0; phase < 3; phase++) {
        setup_tap(true);
        assert(!toggle(true));
        if (phase == 1) {
            now += HOST_INTERACTION_DOUBLE_TAP_TERM_MS;
            host_interaction_housekeeping();
        } else if (phase == 2) {
            assert(!toggle(false));
            now += 50;
            assert(!toggle(true));
        }
        request(HOST_INTERACTION_RELEASE_SESSION, false);
        submit(HOST_INTERACTION_RESULT_OK);
        assert(!toggle(false));
        assert(replayed == 0);
        assert(toggle(true) && toggle(false));
    }

    // Watchdog wins over a due callback.
    setup_tap(true);
    request(HOST_INTERACTION_KEEPALIVE, false);
    submit(HOST_INTERACTION_RESULT_OK);
    now += HOST_INTERACTION_HEARTBEAT_TIMEOUT_MS - 100;
    assert(!toggle(true) && !toggle(false));
    now += HOST_INTERACTION_DOUBLE_TAP_TERM_MS;
    host_interaction_housekeeping();
    assert(!host_interaction_protocol_session_alive() && replayed == 0);

    // Host-force scopes cannot arm the single-tap callback.
    setup_tap(false);
    request(HOST_INTERACTION_BEGIN_FORCE_SCOPE, false);
    put16(&packet[9], 1);
    put16(&packet[11], 1);
    packet[13] = HOST_INTERACTION_FORCE_ALL_CONFIGURED;
    put16(&packet[14], 2000);
    submit(HOST_INTERACTION_RESULT_OK);
    request(HOST_INTERACTION_COMMIT_FORCE_SCOPE, false);
    put16(&packet[9], 1);
    submit(HOST_INTERACTION_RESULT_OK);
    host_interaction_resolved_binding_t binding;
    assert(!host_interaction_protocol_resolve_binding(HOST_INTERACTION_PAUSE_CONTROL, &binding));
    request(HOST_INTERACTION_RELEASE_SESSION, false);
    submit(HOST_INTERACTION_RESULT_OK);
}
int main(void) {
    const uint16_t ordinary = HOST_INTERACTION_CONTROL_ID(HOST_INTERACTION_CONTROL_KEY, 1, 15);
    const uint16_t encoder = HOST_INTERACTION_CONTROL_ID(HOST_INTERACTION_CONTROL_ENCODER_CW, 0, 0);
    host_interaction_resolved_binding_t binding;

    // Dimensions and encoder capabilities follow the actual target.
    request(HOST_INTERACTION_GET_CAPABILITIES, true);
    submit(HOST_INTERACTION_RESULT_OK);
    assert(packet[12] == MATRIX_ROWS && packet[13] == MATRIX_COLS);
    assert(packet[14] == EXPECTED_ENCODERS);
    assert(read16(&packet[16]) == MATRIX_ROWS * MATRIX_COLS + EXPECTED_ENCODERS * 2);
    assert(packet[26] & HOST_INTERACTION_FEATURE_TOGGLE_SINGLE_TAP);
    request(HOST_INTERACTION_GET_CAPABILITIES, true);
    packet[3] = 1;
    submit(HOST_INTERACTION_RESULT_UNSUPPORTED_VERSION);
    assert(toggle(true) && toggle(false)); // No host: no interception.
    request(HOST_INTERACTION_CLAIM_SESSION, false);
    submit(HOST_INTERACTION_RESULT_OK);
    generation_request(HOST_INTERACTION_BEGIN_BINDING_REPLACE);
    bind(HOST_INTERACTION_PAUSE_CONTROL, 1, HOST_INTERACTION_RESULT_RESERVED_CONTROL);
    bind(HOST_INTERACTION_CONTROL_ID(0, 0, MATRIX_COLS), 2, HOST_INTERACTION_RESULT_OUT_OF_RANGE);
    bind(encoder, 43, EXPECTED_ENCODERS ? HOST_INTERACTION_RESULT_OK : HOST_INTERACTION_RESULT_OUT_OF_RANGE);
    bind(ordinary, 42, HOST_INTERACTION_RESULT_OK);
    generation_request(HOST_INTERACTION_COMMIT_BINDINGS);
    assert(!host_interaction_protocol_resolve_binding(ordinary, &binding));
    assert(toggle(true) && toggle(false)); // Non-25: immediate ordinary key.

    // Force commits must also respect effect availability.
    request(HOST_INTERACTION_BEGIN_FORCE_SCOPE, false);
    put16(&packet[9], 1);
    put16(&packet[11], 1);
    packet[13] = HOST_INTERACTION_FORCE_ALL_CONFIGURED;
    put16(&packet[14], 2000);
    submit(HOST_INTERACTION_RESULT_OK);
    request(HOST_INTERACTION_COMMIT_FORCE_SCOPE, false);
    put16(&packet[9], 1);
    submit(HOST_INTERACTION_RESULT_INVALID_STATE);
    request(HOST_INTERACTION_CLEAR_FORCE_SCOPE, false);
    submit(HOST_INTERACTION_RESULT_OK);

    host_interaction_on_rgb_effect_changed(true);
    expect_event(HOST_INTERACTION_EVENT_RGB_EFFECT_CHANGED, 1, 0);
    assert(!toggle(true) && !toggle(false));
    now += HOST_INTERACTION_DOUBLE_TAP_TERM_MS;
    host_interaction_housekeeping();
    assert(replayed == 2);
    assert(replay[0].key.row == HOST_INTERACTION_PAUSE_ROW);
    assert(replay[0].key.col == HOST_INTERACTION_PAUSE_COL);
    assert(replay[0].pressed && !replay[1].pressed);

    double_tap();
    expect_event(HOST_INTERACTION_EVENT_MODE_CHANGED, 1, HOST_INTERACTION_PAUSE_CONTROL);
    assert(host_interaction_protocol_resolve_binding(ordinary, &binding));
    assert(binding.binding_id == 42);
    assert(!key(1, 15, true));
    expect_event(HOST_INTERACTION_EVENT_CONTROL_EDGE, HOST_INTERACTION_EDGE_DOWN, ordinary);

    // Disarm preserves held-key routing, session and binding configuration.
    host_interaction_on_rgb_effect_changed(false);
    expect_event(HOST_INTERACTION_EVENT_RGB_EFFECT_CHANGED, 0, 0);
    expect_event(HOST_INTERACTION_EVENT_MODE_CHANGED, 0, HOST_INTERACTION_PAUSE_CONTROL);
    assert(!key(1, 15, false));
    expect_event(HOST_INTERACTION_EVENT_CONTROL_EDGE, HOST_INTERACTION_EDGE_UP, ordinary);
    assert(host_interaction_protocol_session_alive());
    assert(!host_interaction_protocol_resolve_binding(ordinary, &binding));
    host_interaction_on_rgb_effect_changed(true);
    expect_event(HOST_INTERACTION_EVENT_RGB_EFFECT_CHANGED, 1, 0);
    assert(!host_interaction_protocol_resolve_binding(ordinary, &binding));
    double_tap();
    expect_event(HOST_INTERACTION_EVENT_MODE_CHANGED, 1, HOST_INTERACTION_PAUSE_CONTROL);

    if (EXPECTED_ENCODERS) {
        keyrecord_t record = {.event = {.key = {0, 0}, .pressed = true, .type = ENCODER_CW_EVENT}};
        assert(!host_interaction_pre_process_record(0, &record));
        expect_event(HOST_INTERACTION_EVENT_CONTROL_EDGE, HOST_INTERACTION_EDGE_DOWN, encoder);
        record.event.pressed = false;
        assert(!host_interaction_pre_process_record(0, &record));
        expect_event(HOST_INTERACTION_EVENT_CONTROL_EDGE, HOST_INTERACTION_EDGE_UP, encoder);
    }

    now += HOST_INTERACTION_HEARTBEAT_TIMEOUT_MS;
    host_interaction_housekeeping();
    assert(!host_interaction_protocol_session_alive());
    assert(key(1, 15, true) && key(1, 15, false));
    assert(toggle(true) && toggle(false));
    single_tap_capture_tests();
    printf("PASS: %dx%d, %d encoder(s), toggle [%d,%d]\n",
           MATRIX_ROWS, MATRIX_COLS, EXPECTED_ENCODERS,
           HOST_INTERACTION_PAUSE_ROW, HOST_INTERACTION_PAUSE_COL);
    return 0;
}
