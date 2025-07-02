from typing import Optional, Tuple

from .net_config import stick_deadzone
from dataclasses import dataclass, field
from . import net_config as net_cfg

# Mapping tables for battery levels and connection type values
BATTERY_STATES = {
    0x00: "Not applicable",
    0x01: "Dying",
    0x02: "Low",
    0x03: "Medium",
    0x04: "High",
    0x05: "Full (or almost)",
    0xEE: "Charging",
    0xEF: "Charged",
}

CONNECTION_TYPES = {
    0: "N/A",
    1: "USB",
    2: "Bluetooth",
}

def button_mask_1(share=False, l3=False, r3=False, options=False, up=False, right=False, down=False, left=False):
    return (
        (0x01 if share else 0) |
        (0x02 if l3 else 0) |
        (0x04 if r3 else 0) |
        (0x08 if options else 0) |
        (0x10 if up else 0) |
        (0x20 if right else 0) |
        (0x40 if down else 0) |
        (0x80 if left else 0)
    )

def button_mask_2(l2=False, r2=False, l1=False, r1=False, triangle=False, circle=False, cross=False, square=False):
    return (
        (0x01 if l2 else 0) |
        (0x02 if r2 else 0) |
        (0x04 if l1 else 0) |
        (0x08 if r1 else 0) |
        (0x10 if triangle else 0) |
        (0x20 if circle else 0) |
        (0x40 if cross else 0) |
        (0x80 if square else 0)
    )

def touchpad_input(active=False, touch_id=0, x=0, y=0):
    return (
        1 if active else 0,
        touch_id & 0xFF,
        x & 0xFFFF,
        y & 0xFFFF
    )

@dataclass
class ControllerState:
    """Current virtual controller state."""

    # Match DS4Windows behaviour by defaulting to a disconnected state.
    connected: bool = False
    packet_num: int = 0

    buttons1: int = button_mask_1()
    buttons2: int = button_mask_2()
    home: bool = False
    touch_button: bool = False
    # Default axes match a centred physical controller stick.
    L_stick: Tuple[int, int] = (128, 128)
    R_stick: Tuple[int, int] = (128, 128)

    dpad_analog: Tuple[int, int, int, int] = (0, 0, 0, 0)
    face_analog: Tuple[int, int, int, int] = (0, 0, 0, 0)

    analog_R1: int = 0
    analog_L1: int = 0
    analog_R2: int = 0
    analog_L2: int = 0

    touchpad_input1: Optional[Tuple[int, int, int, int]] = None
    touchpad_input2: Optional[Tuple[int, int, int, int]] = None

    motion_timestamp: int = 0
    accelerometer: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    gyroscope: Tuple[float, float, float] = (0.0, 0.0, 0.0)

    # Additional metadata from the originating server
    connection_type: int = 2
    battery: int = 5

    # Mark slot as idle. When ``True`` the server keeps the slot connected
    # even if no inputs are active so it can be used as a buffer.
    idle: bool = False

    # Number of rumble motors and their intensities
    motor_count: int = net_cfg.motor_count
    motors: Tuple[int, ...] = field(
        default_factory=lambda: (0,) * net_cfg.motor_count
    )
    motor_timestamps: Tuple[float, ...] = field(
        default_factory=lambda: (0.0,) * net_cfg.motor_count
    )

    def is_idle(self, dz: int = stick_deadzone) -> bool:
        """Return ``True`` if no buttons, sticks, triggers or touches are active.

        ``dz`` specifies a deadzone tolerance for stick axes.
        """
        no_buttons = self.buttons1 == button_mask_1() and self.buttons2 == button_mask_2()
        no_misc = not (self.home or self.touch_button)
        # Sticks are considered centred if they are within ``dz`` of the
        # expected neutral value (128) on both axes.
        sticks_centered = (
            abs(self.L_stick[0] - 128) <= dz
            and abs(self.L_stick[1] - 128) <= dz
            and abs(self.R_stick[0] - 128) <= dz
            and abs(self.R_stick[1] - 128) <= dz
        )
        dpads_zero = self.dpad_analog == (0, 0, 0, 0) and self.face_analog == (0, 0, 0, 0)
        triggers_zero = all(v == 0 for v in (self.analog_R1, self.analog_L1, self.analog_R2, self.analog_L2))
        touches_inactive = (
            (self.touchpad_input1 is None or self.touchpad_input1[0] == 0)
            and (self.touchpad_input2 is None or self.touchpad_input2[0] == 0)
        )
        return all([no_buttons, no_misc, sticks_centered, dpads_zero, triggers_zero, touches_inactive])

    def update_connection(self, dz: int = stick_deadzone) -> None:
        """Synchronize ``connected`` with current input state using ``dz`` as the stick deadzone.

        When :attr:`idle` is ``True`` the slot remains connected regardless of
        activity.  Otherwise connection is based on :meth:`is_idle`.
        """
        self.connected = self.idle or not self.is_idle(dz)


class ControllerStateDict(dict):
    """Mapping of controller slot numbers to :class:`ControllerState`.

    Accessing a missing slot automatically creates a default
    :class:`ControllerState` without starting a controller thread. This
    allows ``controller_states[slot].idle = True`` to work for new slots.
    """

    def __missing__(self, key: int) -> ControllerState:
        if not isinstance(key, int) or key < 0:
            raise KeyError(key)
        net_cfg.ensure_slot_count(key + 1)
        value = ControllerState(connected=False)
        self[key] = value
        return value
