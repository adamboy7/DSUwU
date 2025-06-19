import time
from libraries.inputs import frame_delay
from libraries.masks import button_mask_1, button_mask_2

try:
    import pygame
except ImportError:  # pragma: no cover - pygame might not be installed
    pygame = None


def _scale_axis(value: float) -> int:
    """Convert a pygame axis value (-1..1) to DSU range 0..255."""
    value = max(-1.0, min(1.0, value))
    return int((value + 1.0) * 127.5)


def controller_loop(stop_event, controller_states, slot):
    if pygame is None:
        raise RuntimeError("pygame is required for the hardware controller script")
    pygame.init()
    pygame.joystick.init()

    if pygame.joystick.get_count() == 0:
        raise RuntimeError("No joystick detected")

    js = pygame.joystick.Joystick(0)
    js.init()

    try:
        while not stop_event.is_set():
            pygame.event.pump()
            state = controller_states[slot]

            # D-pad via first hat
            hat_x = 0
            hat_y = 0
            if js.get_numhats() > 0:
                hat_x, hat_y = js.get_hat(0)

            state.buttons1 = button_mask_1(
                share=js.get_button(8),
                l3=js.get_button(10),
                r3=js.get_button(11),
                options=js.get_button(9),
                up=hat_y > 0,
                right=hat_x > 0,
                down=hat_y < 0,
                left=hat_x < 0,
            )

            state.buttons2 = button_mask_2(
                l2=js.get_button(6),
                r2=js.get_button(7),
                l1=js.get_button(4),
                r1=js.get_button(5),
                triangle=js.get_button(3),
                circle=js.get_button(2),
                cross=js.get_button(1),
                square=js.get_button(0),
            )

            state.home = bool(js.get_button(12))
            state.touch_button = bool(js.get_button(13))

            state.L_stick = (
                _scale_axis(js.get_axis(0)),
                _scale_axis(js.get_axis(1)),
            )
            rs_y_axis = 3 if js.get_numaxes() <= 4 else 4
            state.R_stick = (
                _scale_axis(js.get_axis(2)),
                _scale_axis(js.get_axis(rs_y_axis)),
            )

            if js.get_numaxes() >= 5:
                state.analog_L2 = _scale_axis(js.get_axis(3))
            else:
                state.analog_L2 = 255 if js.get_button(6) else 0

            if js.get_numaxes() >= 6:
                state.analog_R2 = _scale_axis(js.get_axis(4))
            else:
                state.analog_R2 = 255 if js.get_button(7) else 0

            time.sleep(frame_delay)
    finally:
        js.quit()
        pygame.joystick.quit()
        pygame.quit()

