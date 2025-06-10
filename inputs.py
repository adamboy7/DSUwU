from masks import *
import time
import threading

press_duration = 3
cycle_duration = 60
frame_delay = 1 / 60.0

def pulse_inputs(frame, controller_states, slot,
                 press_duration=press_duration,
                 cycle_duration=cycle_duration):
    """Update the state for a single controller slot.

    The controller_states mapping is still provided so implementations can
    optionally interact with other slots if desired, but the default behavior
    only touches the specified *slot*.
    """

    if frame % cycle_duration < press_duration:
        controller_states[slot].buttons2 = button_mask_2(
            circle=(slot == 0),
            cross=(slot == 1),
            square=(slot == 2),
            triangle=(slot == 3)
        )
    else:
        controller_states[slot].buttons2 = button_mask_2()


def controller_loop(stop_event, controller_states, slot):
    """Periodically update the state for a single controller slot."""
    frame = 0
    while not stop_event.is_set():
        pulse_inputs(frame, controller_states, slot)
        frame += 1
        time.sleep(frame_delay)
