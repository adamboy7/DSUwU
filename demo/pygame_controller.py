import time
import pygame

from libraries.inputs import frame_delay
from libraries.masks import button_mask_1, button_mask_2

# Index of the pygame joystick to read input from. Increase this if you
# have multiple controllers connected and want to use one beyond the
# first.
JOYSTICK_INDEX = 0


def _axis_to_byte(value: float) -> int:
    """Convert a pygame axis value (-1.0..1.0) to an unsigned byte."""
    v = int((value + 1.0) * 127.5)
    return max(0, min(v, 255))


def controller_loop(stop_event, controller_states, slot):
    """Capture gamepad input using pygame and update ``controller_states``."""
    pygame.init()
    pygame.joystick.init()
    if pygame.joystick.get_count() == 0:
        print("pygame controller script: no joystick detected")
        return

    if pygame.joystick.get_count() <= JOYSTICK_INDEX:
        print(
            f"pygame controller script: joystick {JOYSTICK_INDEX} not available"
        )
        return

    js = pygame.joystick.Joystick(JOYSTICK_INDEX)
    js.init()

    while not stop_event.is_set():
        pygame.event.pump()
        state = controller_states[slot]
        state.connected = True

        buttons = [js.get_button(i) for i in range(min(js.get_numbuttons(), 16))]
        # Extend list to 16 elements
        while len(buttons) < 16:
            buttons.append(0)

        state.buttons1 = button_mask_1(
            share=bool(buttons[4]),
            l3=bool(buttons[7]),
            r3=bool(buttons[8]),
            options=bool(buttons[6]),
            up=bool(buttons[11]),
            right=bool(buttons[14]),
            down=bool(buttons[12]),
            left=bool(buttons[13]),
        )
        state.buttons2 = button_mask_2(
            l1=bool(buttons[9]),
            r1=bool(buttons[10]),
            triangle=bool(buttons[3]),
            circle=bool(buttons[1]),
            cross=bool(buttons[0]),
            square=bool(buttons[2]),
        )
        state.home = bool(buttons[5])
        state.touch_button = bool(buttons[15])
        state.analog_L1 = 255 if buttons[9] else 0
        state.analog_R1 = 255 if buttons[10] else 0

        axes = [js.get_axis(i) for i in range(js.get_numaxes())]
        # Left stick
        if len(axes) >= 2:
            state.L_stick = (_axis_to_byte(axes[0]), _axis_to_byte(axes[1]))
        if len(axes) >= 4:
            state.R_stick = (_axis_to_byte(axes[2]), _axis_to_byte(axes[3]))
        if len(axes) >= 5:
            state.analog_L2 = _axis_to_byte(axes[4])
        if len(axes) >= 6:
            state.analog_R2 = _axis_to_byte(axes[5])

        time.sleep(frame_delay)

    js.quit()
