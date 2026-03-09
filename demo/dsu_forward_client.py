"""Mirror a remote DSU slot into the DSUwU server.

This controller script connects to another DSU server, requests input for a
configurable slot, and forwards the received state into the local DSUwU server.

Configuration variables (edit below if needed):
- ``SERVER_IP``: DSU server IP address.
- ``SERVER_PORT``: DSU server UDP port.
- ``REMOTE_SLOT``: Slot number to request from the remote DSU server.
- ``REQUEST_INTERVAL``: Seconds between request packets.
- ``FORWARD_MOTION``: Forward accelerometer/gyroscope data into the local slot.
  Set to False to zero out motion fields regardless of what the remote sends.
- ``FORWARD_TOUCH``: Forward touchpad contact data into the local slot.
  Set to False to clear touch fields regardless of what the remote sends.
"""
from __future__ import annotations

import socket
import struct
import time
from typing import Any, Dict

import libraries.net_config as net_cfg
from protocols.dsu_constants import (
    DSU_button_request,
    DSU_button_response,
    DSU_list_ports,
    DSU_version_request,
    PROTOCOL_VERSION,
)
from protocols.dsu_packet import crc_packet


SERVER_IP = "127.0.0.1"
SERVER_PORT = net_cfg.UDP_port
REMOTE_SLOT = 0
REQUEST_INTERVAL = 0.25

# ---------------------------------------------------------------------------
# Motion / touch forwarding — toggle what gets written into the local slot
# ---------------------------------------------------------------------------

# Copy accelerometer and gyroscope data from the remote slot.
# Set to False to zero out motion fields in the local controller state.
FORWARD_MOTION: bool = True

# Copy touchpad contact data (position and active state) from the remote slot.
# Set to False to clear touch fields in the local controller state.
FORWARD_TOUCH: bool = True


def build_client_packet(msg_type: int, payload: bytes, protocol_version: int) -> bytes:
    """Return a DSU client packet for ``msg_type`` and ``payload``."""

    msg = struct.pack("<I", msg_type) + payload
    length = len(msg)
    header = struct.pack("<4sHHII", b"DSUC", protocol_version, length, 0, 0)
    crc = crc_packet(header, msg)
    header = struct.pack("<4sHHII", b"DSUC", protocol_version, length, crc, 0)
    return header + msg


def decode_buttons(buttons1: int, buttons2: int) -> dict[str, bool]:
    """Return ordered boolean mapping for the 16 button bits."""

    return {
        "D-Pad Left": bool(buttons1 & 0x80),
        "D-Pad Down": bool(buttons1 & 0x40),
        "D-Pad Right": bool(buttons1 & 0x20),
        "D-Pad Up": bool(buttons1 & 0x10),
        "Options": bool(buttons1 & 0x08),
        "R3": bool(buttons1 & 0x04),
        "L3": bool(buttons1 & 0x02),
        "Share": bool(buttons1 & 0x01),
        "Triangle": bool(buttons2 & 0x10),
        "Circle":   bool(buttons2 & 0x20),
        "Cross":    bool(buttons2 & 0x40),
        "Square":   bool(buttons2 & 0x80),
        "R1": bool(buttons2 & 0x08),
        "L1": bool(buttons2 & 0x04),
        "R2": bool(buttons2 & 0x02),
        "L2": bool(buttons2 & 0x01),
    }


def decode_touch(raw: tuple[int, int, int, int]) -> dict[str, Any]:
    """Return mapping with active flag, id and position from touch tuple."""

    active, touch_id, x, y = raw
    return {"active": bool(active), "id": touch_id, "pos": (x, y)}


def parse_button_response(data: bytes) -> Dict[str, Any] | None:
    """Decode a DSU button response into a mapping of controller fields."""

    if len(data) < 20:
        return None
    if data[:4] != b"DSUS":
        return None
    recv_crc, = struct.unpack_from("<I", data, 8)
    if crc_packet(data[:16], data[16:]) != recv_crc:
        return None
    protocol_version, = struct.unpack_from("<H", data, 4)
    msg_type, = struct.unpack_from("<I", data, 16)
    if msg_type != DSU_button_response:
        return None

    payload = data[20:]
    # slot(1), slot_state(1), device_model(1), connection_type(1), mac(6), battery(1), is_active(1)
    fmt_hdr = "<4B6s2B"
    hdr_size = struct.calcsize(fmt_hdr)
    if len(payload) < hdr_size + 4:
        return None

    slot, _slot_state, _device_model, connection_type, mac, battery, is_active = struct.unpack(
        fmt_hdr, payload[:hdr_size]
    )
    offset = hdr_size

    # 4-byte packet counter follows the header.
    counter, = struct.unpack_from("<I", payload, offset)
    offset += 4

    # buttons1, buttons2, home, touch_button,
    # ls_x, ls_y_inv, rs_x, rs_y_inv,
    # dpad_left, dpad_down, dpad_right, dpad_up,
    # face_sqr, face_cro, face_cir, face_tri,
    # analog_r1, analog_l1, analog_r2, analog_l2
    fmt_btns = "<BBBBBBBBBBBBBBBBBBBB"
    btn_size = struct.calcsize(fmt_btns)
    if len(payload) < offset + btn_size:
        return None
    (
        buttons1, buttons2, home, touch_button,
        ls_x, ls_y_inv, rs_x, rs_y_inv,
        dpad_left, dpad_down, dpad_right, dpad_up,
        face_sqr, face_cro, face_cir, face_tri,
        analog_r1, analog_l1, analog_r2, analog_l2,
    ) = struct.unpack(fmt_btns, payload[offset:offset + btn_size])
    offset += btn_size

    # Touch packets: active(B), id(B), x(H), y(H) — 6 bytes each.
    touch_fmt = "<BBHH"
    touch_size = struct.calcsize(touch_fmt)
    if len(payload) < offset + 2 * touch_size + 8 + 24:
        return None

    touch1 = decode_touch(struct.unpack(touch_fmt, payload[offset:offset + touch_size]))
    touch2 = decode_touch(struct.unpack(touch_fmt, payload[offset + touch_size:offset + 2 * touch_size]))
    offset += 2 * touch_size

    motion_ts, = struct.unpack_from("<Q", payload, offset)
    offset += 8
    accel_gyro_fmt = "<6f"
    if len(payload) < offset + struct.calcsize(accel_gyro_fmt):
        return None
    accel_x, accel_y, accel_z, gyro_x, gyro_y, gyro_z = struct.unpack_from(accel_gyro_fmt, payload, offset)

    return {
        "slot": slot,
        "mac": ":".join(f"{b:02X}" for b in mac),
        "packet": counter,
        "protocol_version": protocol_version,
        "connected": bool(is_active),
        "connection_type": connection_type,
        "battery": battery,
        "buttons1": buttons1,
        "buttons2": buttons2,
        "buttons": decode_buttons(buttons1, buttons2),
        "home": bool(home),
        "touch_button": bool(touch_button),
        "ls": (ls_x, 255 - ls_y_inv),
        "rs": (rs_x, 255 - rs_y_inv),
        "dpad": (dpad_up, dpad_right, dpad_down, dpad_left),
        "face": (face_sqr, face_cro, face_cir, face_tri),
        "analog_r1": analog_r1,
        "analog_l1": analog_l1,
        "analog_r2": analog_r2,
        "analog_l2": analog_l2,
        "touch1": touch1,
        "touch2": touch2,
        "motion_ts": motion_ts,
        "accel": (accel_x, accel_y, -accel_z),
        "gyro": (gyro_x, gyro_y, gyro_z),
    }


def _copy_state(target_slot: int, controller_states, state: Dict[str, Any]) -> None:
    """Copy decoded DSU state into the local controller slot."""

    controller = controller_states[target_slot]
    dirty_event = controller._dirty_event
    controller._dirty_event = None
    controller.connected = state["connected"]
    controller.packet_num = state["packet"]
    controller.buttons1 = state["buttons1"]
    controller.buttons2 = state["buttons2"]
    controller.home = state["home"]
    controller.touch_button = state["touch_button"]
    controller.L_stick = tuple(state["ls"])
    controller.R_stick = tuple(state["rs"])
    controller.dpad_analog = tuple(state["dpad"])
    controller.face_analog = tuple(state["face"])
    controller.analog_R1 = state["analog_r1"]
    controller.analog_L1 = state["analog_l1"]
    controller.analog_R2 = state["analog_r2"]
    controller.analog_L2 = state["analog_l2"]
    if FORWARD_TOUCH:
        controller.touchpad_input1 = (
            int(state["touch1"]["active"]),
            state["touch1"]["id"],
            *state["touch1"]["pos"],
        )
        controller.touchpad_input2 = (
            int(state["touch2"]["active"]),
            state["touch2"]["id"],
            *state["touch2"]["pos"],
        )
    else:
        controller.touchpad_input1 = (0, 0, 0, 0)
        controller.touchpad_input2 = (0, 0, 0, 0)

    if FORWARD_MOTION:
        controller.motion_timestamp = state["motion_ts"]
        controller.accelerometer = tuple(state["accel"])
        controller.gyroscope = tuple(state["gyro"])
    else:
        controller.motion_timestamp = 0
        controller.accelerometer = (0.0, 0.0, 0.0)
        controller.gyroscope = (0.0, 0.0, 0.0)
    controller.connection_type = state["connection_type"]
    controller.battery = state["battery"]

    try:
        net_cfg.slot_mac_addresses[target_slot] = bytes.fromhex(state["mac"].replace(":", ""))
    except ValueError:
        pass

    controller._dirty_event = dirty_event
    if dirty_event is not None:
        dirty_event.set()


def controller_loop(stop_event, controller_states, slot):
    """Connect to a DSU server and mirror a slot into ``controller_states``."""

    protocol_version = PROTOCOL_VERSION
    addr = (SERVER_IP, SERVER_PORT)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", 0))
    sock.settimeout(0.5)

    target_slot = max(0, REMOTE_SLOT)

    def send(msg_type: int, payload: bytes = b"") -> None:
        packet = build_client_packet(msg_type, payload, protocol_version)
        sock.sendto(packet, addr)

    # Initial handshake
    send(DSU_version_request)
    send(DSU_list_ports, struct.pack("<I", 16) + bytes(range(16)))

    last_request = 0.0
    while not stop_event.is_set():
        now = time.time()
        if now - last_request > REQUEST_INTERVAL:
            registration_payload = struct.pack("<BB6s", 0x01, target_slot, b"\x00" * 6)
            send(DSU_button_request, registration_payload)
            last_request = now

        try:
            data, _ = sock.recvfrom(2048)
        except socket.timeout:
            continue

        state = parse_button_response(data)
        if state is None or state.get("slot") != target_slot:
            continue

        protocol_version = min(protocol_version, state.get("protocol_version", PROTOCOL_VERSION))
        _copy_state(slot, controller_states, state)

    sock.close()
