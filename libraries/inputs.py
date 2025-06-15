"""Input emulation helper utilities."""

import importlib.util

from .masks import button_mask_2

press_duration = 3
cycle_duration = 60
frame_delay = 1 / 60.0


def pulse_button(frame, controller_states, slot, **button_kwargs):
    """Apply a pulsing button mask to ``controller_states``."""
    if frame % cycle_duration < press_duration:
        controller_states[slot].buttons2 = button_mask_2(**button_kwargs)
    else:
        controller_states[slot].buttons2 = button_mask_2()


def load_controller_loop(path):
    """Load a ``controller_loop`` function from ``path``."""
    spec = importlib.util.spec_from_file_location("input_script", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, "controller_loop"):
        raise AttributeError(f"{path!r} does not define 'controller_loop'")
    return module.controller_loop
