import time
from libraries.inputs import frame_delay, press_duration, pulse_button_xor

def controller_loop(stop_event, controller_states, slot):
    while not stop_event.is_set():
        pulse_button_xor(press_duration, controller_states, slot, "square")
        time.sleep(press_duration * frame_delay)
