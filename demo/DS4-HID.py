"""Read controller data via ``hid`` and populate a DSU slot.

This script focuses on DualShock 4 and DualSense style controllers. It mirrors
``demo/pygame_controller.py`` but pulls richer data directly from the HID
reports so touch, motion, battery and MAC information can be forwarded to the
DSUwU server.
"""

from __future__ import annotations

import importlib
import time
from typing import Iterable, Optional

from libraries.inputs import frame_delay, set_slot_mac_address
from libraries.masks import button_mask_1, button_mask_2, touchpad_input


# First controller that matches one of these VID/PID pairs will be used.
SUPPORTED_CONTROLLERS: dict[tuple[int, int], str] = {
    (0x054C, 0x05C4): "DualShock 4",
    (0x054C, 0x09CC): "DualShock 4 (v2)",
    (0x054C, 0x0CE6): "DualSense",
}


def _load_hid_module():
    """Return the ``hid`` module if available, otherwise ``None``."""

    spec = importlib.util.find_spec("hid")
    if spec is None:
        print("hid_controller: missing optional dependency 'hid' (hidapi).")
        print("Install it with 'pip install hidapi' to enable hardware input.")
        return None
    return importlib.import_module("hid")


def _open_controller(hid_module):
    """Open the first supported controller device.

    Returns ``(device, info)`` where ``info`` is the enumeration entry used to
    open the device. If no supported device is present, returns ``(None, None)``.
    """

    for info in hid_module.enumerate():
        key = (info.get("vendor_id"), info.get("product_id"))
        if key not in SUPPORTED_CONTROLLERS:
            continue

        device_cls = getattr(hid_module, "Device", None) or getattr(hid_module, "device", None)
        if device_cls is None:
            print("hid_controller: 'hid' module is missing a Device factory; install hidapi?")
            return None, None

        device_path = info.get("path")
        try:
            try:
                device = device_cls(path=device_path)
            except TypeError:
                device = device_cls()

            if not _ensure_handle_open(device, info, device_path):
                try:
                    device.close()
                except Exception:
                    pass
                continue
        except OSError as exc:
            name = SUPPORTED_CONTROLLERS.get(key, "unknown controller")
            print(f"hid_controller: failed to open {name}: {exc}")
            try:
                device.close()
            except Exception:
                pass
            continue

        name = SUPPORTED_CONTROLLERS.get(key, "controller")
        print(f"hid_controller: connected to {name}")
        return device, info

    return None, None


def _ensure_handle_open(device, info, device_path) -> bool:
    """Ensure a hidapi device handle is open.

    Returns ``True`` if an open handle is available, otherwise ``False``.
    """

    vid = info.get("vendor_id")
    pid = info.get("product_id")
    serial = info.get("serial_number")

    if hasattr(device, "open_path") and device_path is not None:
        try:
            device.open_path(device_path)
            return True
        except Exception as exc:  # pragma: no cover - runtime dependent
            print(f"hid_controller: open_path failed: {exc}")

    if hasattr(device, "open"):
        try:
            device.open(vid, pid, serial=serial) if serial is not None else device.open(vid, pid)
            return True
        except Exception as exc:  # pragma: no cover - runtime dependent
            print(f"hid_controller: device.open failed: {exc}")

    return False


def _button_states(face: int, shoulders: int) -> dict[str, bool]:
    dpad = face & 0x0F
    return {
        "triangle": bool(face & 0x80),
        "circle": bool(face & 0x40),
        "cross": bool(face & 0x20),
        "square": bool(face & 0x10),
        "up": dpad in (0, 1, 7),
        "right": dpad in (1, 2, 3),
        "down": dpad in (3, 4, 5),
        "left": dpad in (5, 6, 7),
        "l1": bool(shoulders & 0x01),
        "r1": bool(shoulders & 0x02),
        "l2": bool(shoulders & 0x04),
        "r2": bool(shoulders & 0x08),
        "share": bool(shoulders & 0x10),
        "options": bool(shoulders & 0x20),
        "l3": bool(shoulders & 0x40),
        "r3": bool(shoulders & 0x80),
    }


def _touch(report: Iterable[int], start: int):
    touch_id = report[start]
    active = (touch_id & 0x80) == 0
    x = ((report[start + 2] & 0x0F) << 8) | report[start + 1]
    y = (report[start + 3] << 4) | (report[start + 2] >> 4)
    return touchpad_input(active, touch_id & 0x7F, x, y)


def _battery_from_power_byte(power: int) -> int:
    """Map the DS4 power byte to DSU battery codes."""

    charging = bool(power & 0x10)
    level = power & 0x0F

    if charging:
        return 0xEF if level >= 8 else 0xEE
    if level == 0:
        return 0x01
    if level <= 2:
        return 0x02
    if level <= 4:
        return 0x03
    if level <= 6:
        return 0x04
    return 0x05


def _connection_from_report(report: list[int]) -> tuple[int, int]:
    """Return ``(base_offset, connection_type)`` for the given report."""

    if report and report[0] in (0x11, 0x15):
        return 2, 2  # Bluetooth reports include a 2-byte header.
    return 1, 1  # USB reports use a 1-byte report ID header.


def controller_loop(stop_event, controller_states, slot):
    hid_module = _load_hid_module()
    if hid_module is None:
        return

    device = None
    device_info: Optional[dict] = None
    last_hw_timestamp: Optional[int] = None
    motion_timestamp = int(time.time() * 1_000_000)

    while not stop_event.is_set():
        if device is None:
            device, device_info = _open_controller(hid_module)
            last_hw_timestamp = None
            motion_timestamp = int(time.time() * 1_000_000)
            if device_info is not None:
                serial = device_info.get("serial_number")
                if serial:
                    try:
                        set_slot_mac_address(slot, serial)
                    except (TypeError, ValueError) as exc:
                        print(f"hid_controller: could not set MAC from serial '{serial}': {exc}")
            if device is None:
                time.sleep(1)
                continue

        try:
            report = device.read(78, timeout_ms=250)
        except ValueError:
            # Handle "not open" and similar errors by attempting to reopen once.
            if not _ensure_handle_open(device, device_info or {}, device_info.get("path") if device_info else None):
                try:
                    device.close()
                except Exception:
                    pass
                device = None
                time.sleep(1)
                continue
            try:
                report = device.read(78, timeout_ms=250)
            except Exception as exc:
                print(f"hid_controller: read failed after reopen: {exc}")
                try:
                    device.close()
                except Exception:
                    pass
                device = None
                time.sleep(1)
                continue
        except OSError as exc:
            print(f"hid_controller: lost device ({exc}), waiting to reconnect...")
            try:
                device.close()
            except Exception:
                pass
            device = None
            time.sleep(1)
            continue

        if not report:
            time.sleep(frame_delay)
            continue

        try:
            base, connection_type = _connection_from_report(report)
        except Exception:
            base, connection_type = 0, 1
        min_length = base + 43  # Covers everything up to the second touch packet.
        if len(report) < min_length:
            continue

        face_byte = report[base + 5]
        shoulder_byte = report[base + 6]
        misc_byte = report[base + 7]

        buttons = _button_states(face_byte, shoulder_byte)
        touch1 = _touch(report, base + 35)
        touch2 = _touch(report, base + 39)

        l2_analog = report[base + 8]
        r2_analog = report[base + 9]

        raw_timestamp = (report[base + 11] << 8) | report[base + 10]
        if last_hw_timestamp is None:
            motion_timestamp = int(time.time() * 1_000_000)
        else:
            delta = (raw_timestamp - last_hw_timestamp) & 0xFFFF
            motion_timestamp += int(delta * (16 / 3))
        last_hw_timestamp = raw_timestamp

        gyro_x = int.from_bytes(bytes(report[base + 13 : base + 15]), "little", signed=True)
        gyro_y = int.from_bytes(bytes(report[base + 15 : base + 17]), "little", signed=True)
        gyro_z = int.from_bytes(bytes(report[base + 17 : base + 19]), "little", signed=True)
        accel_x = int.from_bytes(bytes(report[base + 19 : base + 21]), "little", signed=True)
        accel_y = int.from_bytes(bytes(report[base + 21 : base + 23]), "little", signed=True)
        accel_z = int.from_bytes(bytes(report[base + 23 : base + 25]), "little", signed=True)

        state = controller_states[slot]
        state.connected = True
        state.packet_num = (state.packet_num + 1) & 0xFFFFFFFF
        state.buttons1 = button_mask_1(
            share=buttons["share"],
            l3=buttons["l3"],
            r3=buttons["r3"],
            options=buttons["options"],
            up=buttons["up"],
            right=buttons["right"],
            down=buttons["down"],
            left=buttons["left"],
        )
        state.buttons2 = button_mask_2(
            l2=buttons["l2"] or l2_analog > 0,
            r2=buttons["r2"] or r2_analog > 0,
            l1=buttons["l1"],
            r1=buttons["r1"],
            triangle=buttons["triangle"],
            circle=buttons["circle"],
            cross=buttons["cross"],
            square=buttons["square"],
        )
        state.home = bool(misc_byte & 0x01)
        state.touch_button = bool(misc_byte & 0x02)

        state.L_stick = (report[base + 1], report[base + 2])
        state.R_stick = (report[base + 3], report[base + 4])
        state.dpad_analog = (
            255 if buttons["left"] else 0,
            255 if buttons["down"] else 0,
            255 if buttons["right"] else 0,
            255 if buttons["up"] else 0,
        )
        state.face_analog = (
            255 if buttons["square"] else 0,
            255 if buttons["cross"] else 0,
            255 if buttons["circle"] else 0,
            255 if buttons["triangle"] else 0,
        )

        state.analog_L1 = 255 if buttons["l1"] else 0
        state.analog_R1 = 255 if buttons["r1"] else 0
        state.analog_L2 = l2_analog
        state.analog_R2 = r2_analog

        state.touchpad_input1 = touch1
        state.touchpad_input2 = touch2

        state.motion_timestamp = motion_timestamp
        state.accelerometer = (
            accel_x / 8192.0,
            accel_y / 8192.0,
            accel_z / 8192.0,
        )
        state.gyroscope = (
            gyro_x / 16.0,
            gyro_y / 16.0,
            gyro_z / 16.0,
        )

        state.connection_type = connection_type
        state.battery = _battery_from_power_byte(report[base + 30])

        time.sleep(frame_delay)

    if device is not None:
        try:
            device.close()
        except Exception:
            pass

    if device is not None:
        device.close()
