"""Input emulation helper utilities."""

import importlib.util
import json
import time
import os
import sys
from contextlib import nullcontext
import importlib.abc

from .masks import button_mask_1, button_mask_2
from .masks import touchpad_input
from . import net_config as net_cfg

press_duration = 3
cycle_duration = 60
frame_delay = 1 / 60.0

# Button names grouped by mask
_MASK1_BUTTONS = {
    "share",
    "l3",
    "r3",
    "options",
    "up",
    "right",
    "down",
    "left",
}

_MASK2_BUTTONS = {
    "l2",
    "r2",
    "l1",
    "r1",
    "triangle",
    "circle",
    "cross",
    "square",
}

# Buttons not associated with either mask
_MISC_BUTTONS = {"home", "touch"}

# Exported set of all valid button names
VALID_BUTTONS = _MASK1_BUTTONS | _MASK2_BUTTONS | _MISC_BUTTONS


def pulse_button(frame, controller_states, slot, **button_kwargs):
    """Apply a pulsing button mask to ``controller_states``.

    Supports all buttons from ``button_mask_1`` and ``button_mask_2`` as well as
    the ``home`` and ``touch`` buttons.
    """
    state = controller_states[slot]
    mask1_args = {k: button_kwargs.get(k, False) for k in _MASK1_BUTTONS}
    mask2_args = {k: button_kwargs.get(k, False) for k in _MASK2_BUTTONS}
    home = bool(button_kwargs.get("home", False))
    touch = bool(button_kwargs.get("touch", False))

    if frame % cycle_duration < press_duration:
        state.buttons1 = button_mask_1(**mask1_args)
        state.buttons2 = button_mask_2(**mask2_args)
        state.home = home
        state.touch_button = touch
    else:
        state.buttons1 = button_mask_1()
        state.buttons2 = button_mask_2()
        if home:
            state.home = False
        if touch:
            state.touch_button = False


def pulse_button_xor(frame, controller_states, slot, *buttons, **button_kwargs):
    """Toggle one or more buttons on ``controller_states`` using XOR.

    ``buttons`` may contain button names as positional arguments. Keyword
    arguments are also accepted for backwards compatibility, any truthy
    value enables the corresponding button. Falsy values are ignored.
    Supports all buttons from ``button_mask_1`` and ``button_mask_2`` as well as
    the ``home`` and ``touch`` buttons.
    """
    mask1_args: dict[str, bool] = {}
    mask2_args: dict[str, bool] = {}
    home_toggle = False
    touch_toggle = False

    for b in buttons:
        if b in _MASK1_BUTTONS:
            mask1_args[b] = True
        elif b in _MASK2_BUTTONS:
            mask2_args[b] = True
        elif b == "home":
            home_toggle = True
        elif b == "touch":
            touch_toggle = True

    for k, v in button_kwargs.items():
        if not v:
            continue
        if k in _MASK1_BUTTONS:
            mask1_args[k] = True
        elif k in _MASK2_BUTTONS:
            mask2_args[k] = True
        elif k == "home":
            home_toggle = True
        elif k == "touch":
            touch_toggle = True

    mask1 = button_mask_1(**mask1_args)
    mask2 = button_mask_2(**mask2_args)

    if frame % cycle_duration == 0:
        state = controller_states[slot]
        if mask1:
            state.buttons1 ^= mask1
        if mask2:
            state.buttons2 ^= mask2
        if home_toggle:
            state.home = not state.home
        if touch_toggle:
            state.touch_button = not state.touch_button
    if frame % cycle_duration == press_duration:
        state = controller_states[slot]
        if mask1:
            state.buttons1 ^= mask1
        if mask2:
            state.buttons2 ^= mask2
        if home_toggle:
            state.home = not state.home
        if touch_toggle:
            state.touch_button = not state.touch_button


_loaded_script_names: dict[str, str] = {}
_script_paths: dict[str, str] = {}
_script_counter = 0


class _InputScriptLoader(importlib.abc.Loader):
    """Loader used for dynamically imported controller scripts."""

    def __init__(self, path: str) -> None:
        self.path = path

    def create_module(self, spec):
        return None

    def exec_module(self, module) -> None:
        with open(self.path, "r", encoding="utf-8") as fh:
            source = fh.read()
        exec(compile(source, self.path, "exec"), module.__dict__)


class _InputScriptFinder(importlib.abc.MetaPathFinder):
    """Finder that resolves dynamic controller script modules."""

    def find_spec(self, fullname, path, target=None):
        script_path = _script_paths.get(fullname)
        if script_path:
            return importlib.util.spec_from_file_location(
                fullname,
                script_path,
                loader=_InputScriptLoader(script_path),
            )
        return None


def _sync_env() -> None:
    os.environ["DSUWU_INPUT_SCRIPT_PATHS"] = json.dumps(_script_paths)


def register_input_scripts_from_env() -> None:
    """Load dynamic script mappings from the environment and register finder."""

    env = os.environ.get("DSUWU_INPUT_SCRIPT_PATHS")
    if env:
        try:
            _script_paths.update(json.loads(env))
        except Exception:
            pass

    if not any(isinstance(f, _InputScriptFinder) for f in sys.meta_path):
        sys.meta_path.insert(0, _InputScriptFinder())


# Register mappings at import time so the current process can resolve scripts
register_input_scripts_from_env()


def load_controller_loop(path: str):
    """Load a ``controller_loop`` function from ``path``.

    Each call assigns a unique module name so multiple scripts can be loaded
    without clobbering each other.  This also ensures functions can be pickled
    correctly when ``multiprocessing`` uses ``spawn`` mode (as on Windows).
    """

    global _script_counter

    abs_path = os.path.abspath(path)
    mod_name = _loaded_script_names.get(abs_path)
    if mod_name is None:
        mod_name = f"input_script_{_script_counter}"
        _script_counter += 1
        spec = importlib.util.spec_from_file_location(
            mod_name,
            abs_path,
            loader=_InputScriptLoader(abs_path),
        )
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load controller script: {path!r}")
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception as exc:
            raise ImportError(
                f"failed executing controller script {path!r}: {exc}"
            ) from exc
        sys.modules[mod_name] = module
        _loaded_script_names[abs_path] = mod_name
        _script_paths[mod_name] = abs_path
        _sync_env()
        # Ensure child processes can resolve this script on import
        register_input_scripts_from_env()
    else:
        module = sys.modules[mod_name]

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

    net_cfg.slot_mac_addresses[slot] = mac_bytes


def set_slot_connection_type(controller_states, slot: int, conn_type: int) -> None:
    """Set the connection type for ``slot`` in ``controller_states``.

    ``conn_type`` should be ``-1`` (disconnect), ``0`` (N/A), ``1`` (USB) or
    ``2`` (Bluetooth).
    """

    if conn_type not in (-1, 0, 1, 2):
        raise ValueError("invalid connection type")
    if slot < 0:
        raise ValueError("slot index cannot be negative")

    net_cfg.ensure_slot(slot)
    state = controller_states[slot]
    state.connection_type = conn_type


def Replay_Inputs(path: str, slot: int | str, motion: str | None = None):
    """Return a controller loop that replays captured input and motion data.

    ``path`` should point to a JSON Lines file produced by the viewer's
    input capture feature. ``slot`` specifies which controller slot to
    replay.  Pass ``"all"`` to replay every slot contained in the file.
    ``motion`` may optionally point to a JSON Lines file recorded with the
    motion capture tool to provide accelerometer and gyro data.

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

    def _update_motion(state, entry):
        state.motion_timestamp = entry.get("motion_ts", 0)
        state.accelerometer = tuple(entry.get("accel", (0.0, 0.0, 0.0)))
        state.gyroscope = tuple(entry.get("gyro", (0.0, 0.0, 0.0)))

    def _next_entry(file_handle):
        if file_handle is None:
            return None
        for line in file_handle:
            line = line.strip()
            if not line:
                continue
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
        return None

    def controller_loop(stop_event, controller_states, assigned_slot):
        try:
            fh_inputs = open(path, "r", encoding="utf-8")
        except OSError:
            return
        fh_motion = None
        if motion is not None:
            try:
                fh_motion = open(motion, "r", encoding="utf-8")
            except OSError:
                fh_motion = None

        with fh_inputs, (fh_motion or nullcontext()):
            next_input = _next_entry(fh_inputs)
            next_motion = _next_entry(fh_motion) if fh_motion else None
            prev_time = None
            while not stop_event.is_set() and (next_input or next_motion):
                use_motion = (
                    next_motion is not None
                    and (next_input is None or next_motion.get("time", 0) <= next_input.get("time", 0))
                )
                if use_motion:
                    entry = next_motion
                    next_motion = _next_entry(fh_motion)
                    update = _update_motion
                else:
                    entry = next_input
                    next_input = _next_entry(fh_inputs)
                    update = _update_state

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
                update(controller_states[target_slot], entry)

    return controller_loop


