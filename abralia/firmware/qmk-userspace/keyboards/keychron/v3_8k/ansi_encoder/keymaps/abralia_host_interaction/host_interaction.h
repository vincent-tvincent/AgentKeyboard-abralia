// Copyright 2026 blue_lobster
// SPDX-License-Identifier: GPL-3.0-or-later

#pragma once

#include QMK_KEYBOARD_H

bool host_interaction_pre_process_record(uint16_t keycode, keyrecord_t *record);
void host_interaction_housekeeping(void);
void host_interaction_on_session_reset(void);
