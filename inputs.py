from masks import *
import time

press_duration = 3
cycle_duration = 60
frame_delay = 1 / 60.0

def pulse_inputs(frame, controller_states, slot, press_duration=press_duration, cycle_duration=cycle_duration):
    if frame % cycle_duration < press_duration:
        if slot == 0:
            controller_states[slot].buttons2 = button_mask_2(circle=True)
        elif slot == 1:
            controller_states[slot].buttons2 = button_mask_2(cross=True)
        elif slot == 2:
            controller_states[slot].buttons2 = button_mask_2(square=True)
        elif slot == 3:
            controller_states[slot].buttons2 = button_mask_2(triangle=True)
    else:
        controller_states[slot].buttons2 = button_mask_2()


def controller_loop(stop_event, controller_states, slot):
    frame = 0
    while not stop_event.is_set():
        pulse_inputs(frame, controller_states, slot)
        frame += 1
        time.sleep(frame_delay)
