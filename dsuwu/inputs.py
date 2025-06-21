"""Input emulation helper utilities."""

import importlib.util
import json
import time

from .masks import button_mask_2
from .masks import touchpad_input
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


def pulse_button_xor(frame, controller_states, slot, **button_kwargs):
    """Toggle a button mask on ``controller_states`` using XOR."""
    mask = button_mask_2(**button_kwargs)
    if frame % cycle_duration == 0:
        controller_states[slot].buttons2 ^= mask
    if frame % cycle_duration == press_duration:
        controller_states[slot].buttons2 ^= mask


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


def Replay_Inputs(path: str, slot: int | str):
    """Return a controller loop that replays captured input data.

    ``path`` should point to a JSON Lines file produced by the viewer's
    input capture feature. ``slot`` specifies which controller slot to
    replay.  Pass ``"all"`` to replay every slot contained in the file.

    The returned function matches the ``controller_loop`` signature used
    by :func:`server.start_server`.
    """

    def _update_state(state, entry):
        state.connected = entry.get("connected", False)
        state.buttons1 = entry.get("buttons1", 0)
        state.buttons2 = entry.get("buttons2", 0)
        state.home = entry.get("home", False)
        state.touch_button = entry.get("touch_button", False)
        state.L_stick = tuple(entry.get("ls", (128, 128)))
        state.R_stick = tuple(entry.get("rs", (128, 128)))
        state.dpad_analog = tuple(entry.get("dpad", (0, 0, 0, 0)))
        state.face_analog = tuple(entry.get("face", (0, 0, 0, 0)))
        state.analog_R1 = entry.get("analog_r1", 0)
        state.analog_L1 = entry.get("analog_l1", 0)
        state.analog_R2 = entry.get("analog_r2", 0)
        state.analog_L2 = entry.get("analog_l2", 0)
        t1 = entry.get("touch1") or {"active": False, "id": 0, "pos": (0, 0)}
        t2 = entry.get("touch2") or {"active": False, "id": 0, "pos": (0, 0)}
        state.touchpad_input1 = touchpad_input(
            bool(t1.get("active")), t1.get("id", 0), *t1.get("pos", (0, 0))
        )
        state.touchpad_input2 = touchpad_input(
            bool(t2.get("active")), t2.get("id", 0), *t2.get("pos", (0, 0))
        )

    def controller_loop(stop_event, controller_states, assigned_slot):
        try:
            fh = open(path, "r", encoding="utf-8")
        except OSError:
            return

        with fh:
            prev_time = None
            for line in fh:
                if stop_event.is_set():
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                entry_slot = entry.get("slot", 0)
                if slot != "all" and entry_slot != slot:
                    continue

                if prev_time is not None:
                    delay = entry.get("time", 0.0) - prev_time
                    end = time.time() + max(delay, 0.0)
                    while not stop_event.is_set() and time.time() < end:
                        time.sleep(min(frame_delay, end - time.time()))
                prev_time = entry.get("time", 0.0)

                target_slot = entry_slot if slot == "all" else assigned_slot
                if target_slot not in controller_states:
                    continue
                _update_state(controller_states[target_slot], entry)

    return controller_loop


