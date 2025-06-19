import time

from libraries.inputs import frame_delay
from libraries.masks import button_mask_1, button_mask_2

try:
    import pygame
except ImportError:  # pragma: no cover - pygame might not be installed
    pygame = None


def _scale_axis(value: float, *, centre: bool = False) -> int:
    """Convert a pygame axis value to DSU range 0..255."""
    if centre:
        value = max(-1.0, min(1.0, value))
        return int((value + 1.0) * 127.5)
    # trigger style axis already in 0..1 range
    value = max(0.0, min(1.0, value))
    return int(value * 255)


def controller_loop(stop_event, controller_states, slot):
    """Update ``controller_states[slot]`` using input from the first controller."""
    if pygame is None:
        raise RuntimeError("pygame is required for the hardware controller script")

    pygame.init()

    use_controller = hasattr(pygame, "controller")
    if use_controller:
        pygame.controller.init()
        if pygame.controller.get_count() == 0:
            raise RuntimeError("No controller detected")
        js = pygame.controller.Controller(0)
        js.init()
    else:
        pygame.joystick.init()
        if pygame.joystick.get_count() == 0:
            raise RuntimeError("No joystick detected")
        js = pygame.joystick.Joystick(0)
        js.init()

    try:
        while not stop_event.is_set():
            pygame.event.pump()
            state = controller_states[slot]

            if use_controller:
                state.buttons1 = button_mask_1(
                    share=js.get_button(pygame.CONTROLLER_BUTTON_BACK),
                    l3=js.get_button(pygame.CONTROLLER_BUTTON_LEFTSTICK),
                    r3=js.get_button(pygame.CONTROLLER_BUTTON_RIGHTSTICK),
                    options=js.get_button(pygame.CONTROLLER_BUTTON_START),
                    up=js.get_button(pygame.CONTROLLER_BUTTON_DPAD_UP),
                    right=js.get_button(pygame.CONTROLLER_BUTTON_DPAD_RIGHT),
                    down=js.get_button(pygame.CONTROLLER_BUTTON_DPAD_DOWN),
                    left=js.get_button(pygame.CONTROLLER_BUTTON_DPAD_LEFT),
                )

                state.buttons2 = button_mask_2(
                    l2=js.get_axis(pygame.CONTROLLER_AXIS_TRIGGERLEFT) > 0.1,
                    r2=js.get_axis(pygame.CONTROLLER_AXIS_TRIGGERRIGHT) > 0.1,
                    l1=js.get_button(pygame.CONTROLLER_BUTTON_LEFTSHOULDER),
                    r1=js.get_button(pygame.CONTROLLER_BUTTON_RIGHTSHOULDER),
                    triangle=js.get_button(pygame.CONTROLLER_BUTTON_Y),
                    circle=js.get_button(pygame.CONTROLLER_BUTTON_B),
                    cross=js.get_button(pygame.CONTROLLER_BUTTON_A),
                    square=js.get_button(pygame.CONTROLLER_BUTTON_X),
                )

                state.home = bool(js.get_button(pygame.CONTROLLER_BUTTON_GUIDE))
                state.touch_button = bool(js.get_button(pygame.CONTROLLER_BUTTON_TOUCHPAD))

                state.L_stick = (
                    _scale_axis(js.get_axis(pygame.CONTROLLER_AXIS_LEFTX), centre=True),
                    _scale_axis(js.get_axis(pygame.CONTROLLER_AXIS_LEFTY), centre=True),
                )
                state.R_stick = (
                    _scale_axis(js.get_axis(pygame.CONTROLLER_AXIS_RIGHTX), centre=True),
                    _scale_axis(js.get_axis(pygame.CONTROLLER_AXIS_RIGHTY), centre=True),
                )

                state.analog_L2 = _scale_axis(js.get_axis(pygame.CONTROLLER_AXIS_TRIGGERLEFT))
                state.analog_R2 = _scale_axis(js.get_axis(pygame.CONTROLLER_AXIS_TRIGGERRIGHT))
            else:
                # Fallback: assume a DualShock 4 style layout
                hat_x = hat_y = 0
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
                    l2=js.get_axis(3) > -0.5 if js.get_numaxes() >= 6 else js.get_button(6),
                    r2=js.get_axis(4) > -0.5 if js.get_numaxes() >= 6 else js.get_button(7),
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
                    _scale_axis(js.get_axis(0), centre=True),
                    _scale_axis(js.get_axis(1), centre=True),
                )
                rs_y_axis = 5 if js.get_numaxes() >= 6 else 3
                state.R_stick = (
                    _scale_axis(js.get_axis(2), centre=True),
                    _scale_axis(js.get_axis(rs_y_axis), centre=True),
                )

                if js.get_numaxes() >= 6:
                    state.analog_L2 = _scale_axis(js.get_axis(3))
                    state.analog_R2 = _scale_axis(js.get_axis(4))
                else:
                    state.analog_L2 = 255 if js.get_button(6) else 0
                    state.analog_R2 = 255 if js.get_button(7) else 0

            time.sleep(frame_delay)
    finally:
        js.quit()
        if use_controller:
            pygame.controller.quit()
        else:
            pygame.joystick.quit()
        pygame.quit()

