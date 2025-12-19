"""Merge slot 1 and slot 2 inputs into slot 0.

This script keeps slot 0 connected, XORs the button masks from slots 1 and 2
each frame, and chooses whichever stick/trigger input is farther from neutral
for the merged result (slot 1 wins ties). That makes it possible to treat two
physical controllers as a single combined controller on slot 0.
"""

import time

from libraries.inputs import frame_delay

PRIMARY_STICK_SLOT = 1
SECONDARY_SLOT = 2

NEUTRAL_STICK_VALUE = 128


def _stick_priority(stick):
    """Return a squared distance from neutral to compare stick displacement."""

    dx = stick[0] - NEUTRAL_STICK_VALUE
    dy = stick[1] - NEUTRAL_STICK_VALUE
    return dx * dx + dy * dy


def _pick_stick(primary_stick, secondary_stick):
    """Return the stick farther from neutral, preferring ``primary_stick``."""

    if _stick_priority(secondary_stick) > _stick_priority(primary_stick):
        return secondary_stick
    return primary_stick


def _pick_trigger(primary_trigger, secondary_trigger):
    """Return the trigger value with greater displacement from 0."""

    if abs(secondary_trigger) > abs(primary_trigger):
        return secondary_trigger
    return primary_trigger


def controller_loop(stop_event, controller_states, slot):
    """Continuously merge controller inputs from slots 1 and 2 into ``slot``.

    Slot 1 contributes stick/analog data when it is farther from neutral, while
    button masks are XORed between slots 1 and 2. This intentionally tolerates
    races between writer threads.
    """

    while not stop_event.is_set():
        merged_state = controller_states[slot]
        primary_state = controller_states[PRIMARY_STICK_SLOT]
        secondary_state = controller_states[SECONDARY_SLOT]

        merged_state.connected = True

        merged_state.buttons1 = primary_state.buttons1 ^ secondary_state.buttons1
        merged_state.buttons2 = primary_state.buttons2 ^ secondary_state.buttons2

        merged_state.L_stick = _pick_stick(primary_state.L_stick, secondary_state.L_stick)
        merged_state.R_stick = _pick_stick(primary_state.R_stick, secondary_state.R_stick)

        merged_state.analog_L1 = _pick_trigger(primary_state.analog_L1, secondary_state.analog_L1)
        merged_state.analog_R1 = _pick_trigger(primary_state.analog_R1, secondary_state.analog_R1)
        merged_state.analog_L2 = _pick_trigger(primary_state.analog_L2, secondary_state.analog_L2)
        merged_state.analog_R2 = _pick_trigger(primary_state.analog_R2, secondary_state.analog_R2)

        merged_state.dpad_analog = primary_state.dpad_analog
        merged_state.face_analog = primary_state.face_analog

        merged_state.touchpad_input1 = primary_state.touchpad_input1
        merged_state.touchpad_input2 = primary_state.touchpad_input2

        merged_state.motion_timestamp = primary_state.motion_timestamp
        merged_state.accelerometer = primary_state.accelerometer
        merged_state.gyroscope = primary_state.gyroscope

        merged_state.home = primary_state.home
        merged_state.touch_button = primary_state.touch_button

        merged_state.connection_type = primary_state.connection_type
        merged_state.battery = primary_state.battery

        time.sleep(frame_delay)
