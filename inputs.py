from masks import *

def update_inputs(frame, controller_states, press_duration=3, cycle_duration=60):
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
