from typing import Optional, Tuple
from dataclasses import dataclass

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

    connected: bool = True
    packet_num: int = 0

    buttons1: int = button_mask_1()
    buttons2: int = button_mask_2()
    home: bool = False
    touch_button: bool = False
    L_stick: Tuple[int, int] = (0, 0)
    R_stick: Tuple[int, int] = (0, 0)

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
    battery: int = 5

    # Rumble motor intensities and last update timestamps
    motors: Tuple[int, int] = (0, 0)
    motor_timestamps: Tuple[float, float] = (0.0, 0.0)
