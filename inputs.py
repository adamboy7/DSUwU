from masks import *
import time
import threading


# These values control how inputs are generated over time. Adjust them here
# rather than in ``server.py``.
press_duration = 3
cycle_duration = 60
frame_delay = 1 / 60.0

def update_inputs(frame, controller_states, press_duration=press_duration, cycle_duration=cycle_duration):
    for slot in controller_states:
        if frame % cycle_duration < press_duration:
            controller_states[slot].buttons2 = button_mask_2(
                circle=(slot == 0),
                cross=(slot == 1),
                square=(slot == 2),
                triangle=(slot == 3)
            )
        else:
            controller_states[slot].buttons2 = button_mask_2()


def controller_loop(stop_event, controller_states):
    """Periodically update controller_states for all slots."""
    frame = 0
    while not stop_event.is_set():
        update_inputs(frame, controller_states)
        frame += 1
        time.sleep(frame_delay)
