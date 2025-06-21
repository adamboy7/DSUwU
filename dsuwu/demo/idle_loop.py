import time
from dsuwu.inputs import frame_delay


def controller_loop(stop_event, controller_states, slot):
    controller_states[slot].idle = True
    while not stop_event.is_set():
        time.sleep(frame_delay)

