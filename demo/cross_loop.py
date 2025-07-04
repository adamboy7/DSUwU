import time
from libraries.inputs import frame_delay, pulse_button_xor

def controller_loop(stop_event, controller_states, slot):
    frame = 0
    while not stop_event.is_set():
        pulse_button_xor(frame, controller_states, slot, "cross")
        frame += 1
        time.sleep(frame_delay)
