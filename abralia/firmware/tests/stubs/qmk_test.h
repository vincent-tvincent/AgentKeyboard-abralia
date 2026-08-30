// Copyright 2026 blue_lobster
// SPDX-License-Identifier: GPL-3.0-or-later

#pragma once
#include <stdbool.h>
#include <stdint.h>

typedef struct { uint8_t row, col; } keypos_t;
typedef struct { keypos_t key; bool pressed; uint8_t type; } keyevent_t;
typedef struct { keyevent_t event; } keyrecord_t;
enum { KEY_EVENT, ENCODER_CW_EVENT, ENCODER_CCW_EVENT };
#define IS_KEYEVENT(event) ((event).type == KEY_EVENT)
#define MAKE_KEYEVENT(r, c, down) \
    ((keyevent_t){.key = {(r), (c)}, .pressed = (down), .type = KEY_EVENT})
#define RAW_EPSIZE 32
enum { id_custom_channel = 0, id_custom_set_value = 7, id_custom_get_value = 8 };
uint32_t timer_read32(void);
uint32_t timer_elapsed32(uint32_t started);
void raw_hid_send(uint8_t *report, uint8_t length);
void action_exec(keyevent_t event);
