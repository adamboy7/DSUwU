import time
from libraries.inputs import frame_delay, pulse_button

def controller_loop(stop_event, controller_states, slot):
    frame = 0
    while not stop_event.is_set():
        pulse_button(frame, controller_states, slot, square=True)
        frame += 1
        time.sleep(frame_delay)
