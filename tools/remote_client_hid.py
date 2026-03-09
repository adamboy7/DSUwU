"""HID-based remote controller client for DSUwU remote play.

Reads input from a DualShock 4 or DualSense controller via the ``hid``
(hidapi) module and pushes full DSU button response packets — including
accelerometer, gyroscope, and touchpad data — over UDP to a DSUwU server
running ``demo/remote_input_script.py``.

Usage:
    python tools/remote_client_hid.py

Server IP, port, and slot are read from ``tools/remote_config.py``.
Toggle SEND_MOTION and SEND_TOUCH below to control what data is forwarded.

Requirements:
    pip install hidapi
"""

from __future__ import annotations

import importlib
import importlib.util
import socket
import struct
import time
import zlib
from typing import Optional, Sequence

import remote_config

# ---------------------------------------------------------------------------
# Configuration — toggle motion and touch forwarding here
# ---------------------------------------------------------------------------

# Forward accelerometer and gyroscope data to the server.
SEND_MOTION: bool = True

# Forward touchpad contact data (position and active state) to the server.
SEND_TOUCH: bool = True

# ---------------------------------------------------------------------------
# Shared config from remote_config.py
# ---------------------------------------------------------------------------

SERVER_IP   = remote_config.SERVER_IP
SERVER_PORT = remote_config.SERVER_PORT
SLOT        = remote_config.SLOT

# ---------------------------------------------------------------------------
# Protocol constants
# ---------------------------------------------------------------------------

_PROTOCOL_VERSION    = 1001
_DSU_BUTTON_RESPONSE = 0x100002

# Supported DS4/DualSense VID/PID pairs.
SUPPORTED_CONTROLLERS: dict[tuple[int, int], str] = {
    (0x054C, 0x05C4): "DualShock 4",
    (0x054C, 0x09CC): "DualShock 4 (v2)",
    (0x054C, 0x0CE6): "DualSense",
}

# ---------------------------------------------------------------------------
# Packet-building helpers (mirroring protocols/dsu_packet.py)
# ---------------------------------------------------------------------------

def _crc_packet(header: bytes, msg: bytes) -> int:
    data = header[:8] + b"\x00\x00\x00\x00" + header[12:] + msg
    return zlib.crc32(data) & 0xFFFFFFFF


def _button_mask_1(
    share=False, l3=False, r3=False, options=False,
    up=False, right=False, down=False, left=False,
) -> int:
    return (
        (0x01 if share else 0)   | (0x02 if l3 else 0) |
        (0x04 if r3 else 0)      | (0x08 if options else 0) |
        (0x10 if up else 0)      | (0x20 if right else 0) |
        (0x40 if down else 0)    | (0x80 if left else 0)
    )


def _button_mask_2(
    l2=False, r2=False, l1=False, r1=False,
    triangle=False, circle=False, cross=False, square=False,
) -> int:
    return (
        (0x01 if l2 else 0)       | (0x02 if r2 else 0) |
        (0x04 if l1 else 0)       | (0x08 if r1 else 0) |
        (0x10 if triangle else 0) | (0x20 if circle else 0) |
        (0x40 if cross else 0)    | (0x80 if square else 0)
    )


def _touchpad_input(active: bool = False, touch_id: int = 0, x: int = 0, y: int = 0) -> tuple:
    return (1 if active else 0, touch_id & 0xFF, x & 0xFFFF, y & 0xFFFF)


def _build_packet(
    slot: int,
    packet_num: int,
    buttons1: int,
    buttons2: int,
    home: bool,
    touch_button: bool,
    L_stick: tuple,
    R_stick: tuple,
    analog_L1: int,
    analog_R1: int,
    analog_L2: int,
    analog_R2: int,
    dpad_analog: tuple = (0, 0, 0, 0),   # (up, right, down, left)
    face_analog: tuple = (0, 0, 0, 0),   # (square, cross, circle, triangle)
    touch1: tuple = (0, 0, 0, 0),        # (active, id, x, y)
    touch2: tuple = (0, 0, 0, 0),
    motion_timestamp: int = 0,
    accelerometer: tuple = (0.0, 0.0, 0.0),
    gyroscope: tuple = (0.0, 0.0, 0.0),
    connection_type: int = 2,
    battery: int = 5,
    mac: bytes = b"\xCC\xCC\xCC\xCC\xCC\x02",
) -> bytes:
    ls_x, ls_y = L_stick
    rs_x, rs_y = R_stick
    dpad_up, dpad_right, dpad_down, dpad_left = dpad_analog
    t1_active, t1_id, t1_x, t1_y = touch1
    t2_active, t2_id, t2_x, t2_y = touch2
    accel_x, accel_y, accel_z = accelerometer
    gyro_x, gyro_y, gyro_z = gyroscope

    payload = struct.pack(
        "<4B6s2B",
        slot, 2, 2, connection_type, mac, battery, 1,
    )
    payload += struct.pack("<I", packet_num)
    payload += struct.pack(
        "<BBBBBBBBBBBBBBBBBBBB",
        buttons1, buttons2, int(home), int(touch_button),
        ls_x, 255 - ls_y,
        rs_x, 255 - rs_y,
        dpad_left, dpad_down, dpad_right, dpad_up,   # wire order
        *face_analog,                                 # square, cross, circle, triangle
        analog_R1, analog_L1, analog_R2, analog_L2,
    )
    payload += struct.pack("<BBHH", t1_active, t1_id, t1_x, t1_y)
    payload += struct.pack("<BBHH", t2_active, t2_id, t2_x, t2_y)
    payload += struct.pack("<Q", motion_timestamp or int(time.time() * 1_000_000))
    payload += struct.pack("<6f", accel_x, accel_y, -accel_z, gyro_x, gyro_y, gyro_z)

    msg    = struct.pack("<I", _DSU_BUTTON_RESPONSE) + payload
    header = struct.pack("<4sHHII", b"DSUS", _PROTOCOL_VERSION, len(msg), 0, 0)
    crc    = _crc_packet(header, msg)
    header = struct.pack("<4sHHII", b"DSUS", _PROTOCOL_VERSION, len(msg), crc, 0)
    return header + msg


# ---------------------------------------------------------------------------
# HID helpers (based on demo/DS4-HID.py)
# ---------------------------------------------------------------------------

_BT_CRC_SEED = b'\xa1'


def _load_hid():
    """Return the ``hid`` module if available, else print an install hint."""
    spec = importlib.util.find_spec("hid")
    if spec is None:
        print("hidapi is required. Install it with:  pip install hidapi")
        return None
    return importlib.import_module("hid")


def _open_device(hid_module):
    """Open the first supported controller. Returns (device, info) or (None, None)."""
    for info in hid_module.enumerate():
        key = (info.get("vendor_id"), info.get("product_id"))
        if key not in SUPPORTED_CONTROLLERS:
            continue

        device_cls = getattr(hid_module, "Device", None) or getattr(hid_module, "device", None)
        if device_cls is None:
            print("hid: 'hid' module is missing a Device factory; install hidapi?")
            return None, None

        path = info.get("path")
        try:
            device = device_cls(path=path) if path else device_cls()
            if not _ensure_open(device, info, path):
                try:
                    device.close()
                except Exception:
                    pass
                continue
        except OSError as exc:
            name = SUPPORTED_CONTROLLERS.get(key, "controller")
            print(f"hid: could not open {name}: {exc}")
            continue

        print(f"hid: connected to {SUPPORTED_CONTROLLERS[key]}")
        return device, info

    return None, None


def _ensure_open(device, info: dict, path) -> bool:
    """Try open_path then open() as a fallback. Returns True on success."""
    vid, pid, serial = info.get("vendor_id"), info.get("product_id"), info.get("serial_number")

    if hasattr(device, "open_path") and path is not None:
        try:
            device.open_path(path)
            return True
        except Exception:
            pass

    if hasattr(device, "open"):
        try:
            if serial:
                device.open(vid, pid, serial=serial)
            else:
                device.open(vid, pid)
            return True
        except Exception:
            pass

    return False


def _button_states(face: int, shoulders: int) -> dict:
    dpad = face & 0x0F
    return {
        "triangle": bool(face & 0x80), "circle":  bool(face & 0x40),
        "cross":    bool(face & 0x20), "square":  bool(face & 0x10),
        "up":    dpad in (0, 1, 7),    "right": dpad in (1, 2, 3),
        "down":  dpad in (3, 4, 5),    "left":  dpad in (5, 6, 7),
        "l1": bool(shoulders & 0x01),  "r1": bool(shoulders & 0x02),
        "l2": bool(shoulders & 0x04),  "r2": bool(shoulders & 0x08),
        "share":   bool(shoulders & 0x10),
        "options": bool(shoulders & 0x20),
        "l3": bool(shoulders & 0x40),  "r3": bool(shoulders & 0x80),
    }


def _parse_touch(report: Sequence[int], start: int) -> tuple:
    touch_id = report[start]
    active = (touch_id & 0x80) == 0
    x = ((report[start + 2] & 0x0F) << 8) | report[start + 1]
    y = (report[start + 3] << 4) | (report[start + 2] >> 4)
    return _touchpad_input(active, touch_id & 0x7F, x, y)


def _battery(power: int) -> int:
    charging, level = bool(power & 0x10), power & 0x0F
    if charging:
        return 0xEF if level >= 8 else 0xEE
    if level == 0: return 0x01
    if level <= 2: return 0x02
    if level <= 4: return 0x03
    if level <= 6: return 0x04
    return 0x05


def _connection(report: list) -> tuple[int, int]:
    """Return (base_offset, connection_type). 2 = Bluetooth, 1 = USB."""
    if report and report[0] in (0x11, 0x15):
        return 2, 2
    return 0, 1


def _check_bt_crc(report: Sequence[int]) -> bool:
    if len(report) < 78:
        return False
    received = struct.unpack_from("<I", bytes(report), 74)[0]
    computed = zlib.crc32(_BT_CRC_SEED + bytes(report[:74])) & 0xFFFFFFFF
    return received == computed


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main() -> None:
    hid_module = _load_hid()
    if hid_module is None:
        return

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server_addr = (SERVER_IP, SERVER_PORT)

    print(f"Remote HID client → {SERVER_IP}:{SERVER_PORT}  slot={SLOT}")
    print(f"  motion={'on' if SEND_MOTION else 'off'}  touch={'on' if SEND_TOUCH else 'off'}")
    print("Press Ctrl+C to stop.")

    device: Optional[object] = None
    device_info: Optional[dict] = None
    last_hw_ts: Optional[int] = None
    motion_ts   = int(time.time() * 1_000_000)
    last_wall   = time.monotonic()
    packet_num  = 0
    bt_crc_errs = 0
    remote_mac  = b"\xCC\xCC\xCC\xCC\xCC\x02"

    try:
        while True:
            # --- (Re)connect ---------------------------------------------------
            if device is None:
                device, device_info = _open_device(hid_module)
                last_hw_ts = None
                motion_ts  = int(time.time() * 1_000_000)

                if device_info is not None:
                    serial = device_info.get("serial_number")
                    if serial:
                        try:
                            raw = serial.replace(":", "").replace("-", "")
                            remote_mac = bytes.fromhex(raw[:12].ljust(12, "0"))
                        except (ValueError, TypeError):
                            pass

                if device is None:
                    time.sleep(1)
                    continue

            # --- Read HID report -----------------------------------------------
            try:
                report = device.read(78, timeout_ms=4)
            except ValueError:
                # Device closed; try to reopen once before giving up.
                if not _ensure_open(device, device_info or {}, (device_info or {}).get("path")):
                    try:
                        device.close()
                    except Exception:
                        pass
                    device = None
                    time.sleep(1)
                    continue
                try:
                    report = device.read(78, timeout_ms=4)
                except Exception as exc:
                    print(f"hid: read failed after reopen: {exc}")
                    try:
                        device.close()
                    except Exception:
                        pass
                    device = None
                    time.sleep(1)
                    continue
            except OSError as exc:
                print(f"hid: lost device ({exc}), reconnecting…")
                try:
                    device.close()
                except Exception:
                    pass
                device = None
                time.sleep(1)
                continue

            if not report:
                continue

            # --- Parse connection type and report base offset ------------------
            try:
                base, connection_type = _connection(report)
            except Exception:
                base, connection_type = 0, 1

            if connection_type == 2:           # Bluetooth CRC check
                if not _check_bt_crc(report):
                    bt_crc_errs += 1
                    if bt_crc_errs == 10:
                        print("hid: repeated BT CRC failures; check controller connection")
                        bt_crc_errs = 0
                    continue
                bt_crc_errs = 0

            if len(report) < base + 43:
                continue

            # --- Buttons -------------------------------------------------------
            face_byte     = report[base + 5]
            shoulder_byte = report[base + 6]
            misc_byte     = report[base + 7]
            l2_analog     = report[base + 8]
            r2_analog     = report[base + 9]

            buttons = _button_states(face_byte, shoulder_byte)

            # --- Motion timestamp and IMU (raw signed int16) ------------------
            if SEND_MOTION:
                now_wall = time.monotonic()
                raw_ts   = (report[base + 11] << 8) | report[base + 10]
                if last_hw_ts is None:
                    motion_ts = int(time.time() * 1_000_000)
                else:
                    delta = (raw_ts - last_hw_ts) & 0xFFFF
                    if delta != 0:
                        motion_ts += int(delta * (16 / 3))
                    else:
                        motion_ts += int((now_wall - last_wall) * 1_000_000)
                last_hw_ts = raw_ts
                last_wall  = now_wall

                gyro_x  = int.from_bytes(bytes(report[base + 13: base + 15]), "little", signed=True)
                gyro_y  = int.from_bytes(bytes(report[base + 15: base + 17]), "little", signed=True)
                gyro_z  = int.from_bytes(bytes(report[base + 17: base + 19]), "little", signed=True)
                accel_x = int.from_bytes(bytes(report[base + 19: base + 21]), "little", signed=True)
                accel_y = int.from_bytes(bytes(report[base + 21: base + 23]), "little", signed=True)
                accel_z = int.from_bytes(bytes(report[base + 23: base + 25]), "little", signed=True)
            else:
                gyro_x = gyro_y = gyro_z = 0
                accel_x = accel_y = accel_z = 0

            # --- Touch --------------------------------------------------------
            if SEND_TOUCH:
                touch1 = _parse_touch(report, base + 35)
                touch2 = _parse_touch(report, base + 39)
            else:
                touch1 = touch2 = (0, 0, 0, 0)

            # --- Build and send packet ----------------------------------------
            buttons1 = _button_mask_1(
                share=buttons["share"],   l3=buttons["l3"],
                r3=buttons["r3"],         options=buttons["options"],
                up=buttons["up"],         right=buttons["right"],
                down=buttons["down"],     left=buttons["left"],
            )
            buttons2 = _button_mask_2(
                l2=buttons["l2"] or l2_analog > 0,
                r2=buttons["r2"] or r2_analog > 0,
                l1=buttons["l1"],         r1=buttons["r1"],
                triangle=buttons["triangle"], circle=buttons["circle"],
                cross=buttons["cross"],       square=buttons["square"],
            )

            pkt = _build_packet(
                slot=SLOT,
                packet_num=packet_num,
                buttons1=buttons1,
                buttons2=buttons2,
                home=bool(misc_byte & 0x01),
                touch_button=bool(misc_byte & 0x02),
                L_stick=(report[base + 1], report[base + 2]),
                R_stick=(report[base + 3], report[base + 4]),
                analog_L1=255 if buttons["l1"] else 0,
                analog_R1=255 if buttons["r1"] else 0,
                analog_L2=l2_analog,
                analog_R2=r2_analog,
                dpad_analog=(
                    255 if buttons["up"]    else 0,
                    255 if buttons["right"] else 0,
                    255 if buttons["down"]  else 0,
                    255 if buttons["left"]  else 0,
                ),
                face_analog=(
                    255 if buttons["square"]   else 0,
                    255 if buttons["cross"]    else 0,
                    255 if buttons["circle"]   else 0,
                    255 if buttons["triangle"] else 0,
                ),
                touch1=touch1,
                touch2=touch2,
                motion_timestamp=motion_ts if SEND_MOTION else 0,
                accelerometer=(
                    accel_x / 8192.0, accel_y / 8192.0, accel_z / 8192.0,
                ) if SEND_MOTION else (0.0, 0.0, 0.0),
                gyroscope=(
                    gyro_x / 16.0, gyro_y / 16.0, gyro_z / 16.0,
                ) if SEND_MOTION else (0.0, 0.0, 0.0),
                connection_type=connection_type,
                battery=_battery(report[base + 30]),
                mac=remote_mac,
            )

            sock.sendto(pkt, server_addr)
            packet_num = (packet_num + 1) & 0xFFFFFFFF

    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        if device is not None:
            try:
                device.close()
            except Exception:
                pass
        sock.close()


if __name__ == "__main__":
    main()
