// Copyright 2026 blue_lobster
// SPDX-License-Identifier: GPL-3.0-or-later

// The sibling ports retain each board's stock keymap and share Abralia logic.
#ifdef ABRALIA_HOST_INTERACTION
#    include "../v3_8k/ansi_encoder/keymaps/abralia_host_interaction/host_interaction.h"
#    include "../v3_8k/ansi_encoder/keymaps/abralia_host_interaction/host_interaction_protocol.h"
#    include "../v3_8k/ansi_encoder/keymaps/abralia_host_interaction/per_key_rgb_independent_v.h"
#include "via.h"

bool pre_process_record_user(uint16_t keycode, keyrecord_t *record) {
    return host_interaction_pre_process_record(keycode, record);
}

void host_interaction_protocol_reset_hook(void) {
    host_interaction_on_session_reset();
    per_key_rgb_independent_v_host_reset();
}

void per_key_rgb_independent_v_selection_changed(bool selected) {
    host_interaction_on_rgb_effect_changed(selected);
}

void housekeeping_task_user(void) {
    per_key_rgb_independent_v_housekeeping();
    host_interaction_housekeeping();
}

void via_custom_value_command_kb(uint8_t *data, uint8_t length) {
    if (per_key_rgb_independent_v_handle_via(data, length)) {
        return;
    }
    if (host_interaction_protocol_handle_via(data, length)) {
        return;
    }
    data[0] = id_unhandled;
}
#endif // The RGB-only implementation already defines its QMK hooks.
