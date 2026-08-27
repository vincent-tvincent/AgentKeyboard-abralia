RGB_MATRIX_EFFECT(PER_KEY_RGB_INDEPENDENT_V)

#ifdef RGB_MATRIX_CUSTOM_EFFECT_IMPLS

bool PER_KEY_RGB_INDEPENDENT_V(effect_params_t *params) {
    extern bool per_key_rgb_independent_v(effect_params_t * params);
    return per_key_rgb_independent_v(params);
}

#endif
