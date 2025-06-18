"""Input emulation helper utilities."""

import importlib.util

from .masks import button_mask_2
from . import net_config as net_cfg

press_duration = 3
cycle_duration = 60
frame_delay = 1 / 60.0


def pulse_button(frame, controller_states, slot, **button_kwargs):
    """Apply a pulsing button mask to ``controller_states``."""
    if frame % cycle_duration < press_duration:
        controller_states[slot].buttons2 = button_mask_2(**button_kwargs)
    else:
        controller_states[slot].buttons2 = button_mask_2()


def load_controller_loop(path):
    """Load a ``controller_loop`` function from ``path``."""
    spec = importlib.util.spec_from_file_location("input_script", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, "controller_loop"):
        raise AttributeError(f"{path!r} does not define 'controller_loop'")
    return module.controller_loop


def set_slot_mac_address(slot: int, mac: bytes | str) -> None:
    """Update the MAC address for ``slot`` used by scripted inputs.

    ``mac`` may be a 6-byte ``bytes`` object or a string using common MAC
    address notation (``AA:BB:CC:DD:EE:FF`` or ``AABBCCDDEEFF``).  A
    ``ValueError`` is raised if the address is not valid.
    """

    if slot < 0:
        raise ValueError("slot index cannot be negative")

    if isinstance(mac, str):
        hex_str = mac.replace(":", "").replace("-", "").strip()
        if len(hex_str) != 12 or not all(c in "0123456789abcdefABCDEF" for c in hex_str):
            raise ValueError(f"invalid MAC address: {mac!r}")
        mac_bytes = bytes(int(hex_str[i:i + 2], 16) for i in range(0, 12, 2))
    elif isinstance(mac, (bytes, bytearray)):
        mac_bytes = bytes(mac)
        if len(mac_bytes) != 6:
            raise ValueError("MAC address must be exactly 6 bytes")
    else:
        raise TypeError("mac must be bytes or str")

    net_cfg.ensure_slot_count(slot + 1)
    net_cfg.slot_mac_addresses[slot] = mac_bytes

