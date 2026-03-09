"""Lightweight remote controller client for DSUwU remote play.

Captures local gamepad input via pygame and pushes DSU button response
packets over UDP to a DSUwU server running ``demo/remote_input_script.py``.
The client only requires outbound network access — no port forwarding needed.

Usage:
    python demo/remote_client.py

Edit the configuration constants below before running.

Requirements:
    pip install pygame
"""

import socket
import struct
import time
import zlib

# ---------------------------------------------------------------------------
# Configuration — edit these before running
# ---------------------------------------------------------------------------

# IP address of the server machine (the one running DSUwU + remote_input_script.py)
SERVER_IP = "127.0.0.1"

# UDP port to send packets to. Must match LISTEN_PORT in remote_input_script.py.
SERVER_PORT = 26761

# Controller slot to emulate on the server (1–4 for standard DSU).
SLOT = 1

# Which pygame joystick index to use. 0 = first connected gamepad.
JOYSTICK_INDEX = 0

# Packets sent per second. 60 Hz matches the DSU server's update rate.
SEND_RATE = 60

# ---------------------------------------------------------------------------
# Protocol constants (copied from protocols/ so this file is self-contained)
# ---------------------------------------------------------------------------

_PROTOCOL_VERSION = 1001
_DSU_BUTTON_RESPONSE = 0x100002

# MAC address reported for the virtual remote controller.
_REMOTE_MAC = b"\xCC\xCC\xCC\xCC\xCC\x01"


# ---------------------------------------------------------------------------
# Packet-building helpers (mirror of protocols/dsu_packet.py logic)
# ---------------------------------------------------------------------------

def _crc_packet(header: bytes, msg: bytes) -> int:
    """Compute CRC32 over a DSU packet (header + message)."""
    data = header[:8] + b"\x00\x00\x00\x00" + header[12:] + msg
    return zlib.crc32(data) & 0xFFFFFFFF


def _button_mask_1(
    share=False, l3=False, r3=False, options=False,
    up=False, right=False, down=False, left=False,
) -> int:
    return (
        (0x01 if share else 0) | (0x02 if l3 else 0) |
        (0x04 if r3 else 0)   | (0x08 if options else 0) |
        (0x10 if up else 0)   | (0x20 if right else 0) |
        (0x40 if down else 0) | (0x80 if left else 0)
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


def _axis_to_byte(value: float) -> int:
    """Convert a pygame axis value (-1.0 .. 1.0) to an unsigned byte (0–255)."""
    v = int((value + 1.0) * 127.5)
    return max(0, min(v, 255))


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
) -> bytes:
    """Build a DSUS-format button response packet.

    The payload layout exactly mirrors ``protocols/dsu_packet.py:send_input()``
    so that ``demo/dsu_forward_client.py:parse_button_response()`` on the
    server side can decode these packets without modification.
    """
    ls_x, ls_y = L_stick
    rs_x, rs_y = R_stick

    # Slot header (matches send_input payload start)
    payload = struct.pack(
        "<4B6s2B",
        slot,
        2,          # slot state — connected
        2,          # device model — full gyro
        2,          # connection type — Bluetooth
        _REMOTE_MAC,
        5,          # battery — full
        1,          # is_active
    )
    payload += struct.pack("<I", packet_num)
    payload += struct.pack(
        "<BBBBBBBBBBBBBBBBBBBB",
        buttons1, buttons2, int(home), int(touch_button),
        ls_x, 255 - ls_y,   # Y-axis inverted on the wire (matches send_input)
        rs_x, 255 - rs_y,
        0, 0, 0, 0,          # dpad analog (digital dpad is in buttons1)
        0, 0, 0, 0,          # face analog (digital face is in buttons2)
        analog_R1, analog_L1, analog_R2, analog_L2,
    )
    payload += struct.pack("<BBHH", 0, 0, 0, 0)   # touch slot 1 — inactive
    payload += struct.pack("<BBHH", 0, 0, 0, 0)   # touch slot 2 — inactive
    payload += struct.pack("<Q", int(time.time() * 1_000_000))  # motion timestamp
    payload += struct.pack("<6f", 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)  # accel + gyro

    msg = struct.pack("<I", _DSU_BUTTON_RESPONSE) + payload
    header = struct.pack("<4sHHII", b"DSUS", _PROTOCOL_VERSION, len(msg), 0, 0)
    crc = _crc_packet(header, msg)
    header = struct.pack("<4sHHII", b"DSUS", _PROTOCOL_VERSION, len(msg), crc, 0)
    return header + msg


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main() -> None:
    try:
        import pygame
    except ImportError:
        print("pygame is required. Install it with:  pip install pygame")
        return

    pygame.init()
    pygame.joystick.init()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server_addr = (SERVER_IP, SERVER_PORT)
    send_interval = 1.0 / SEND_RATE
    packet_num = 0

    print(f"Remote client → {SERVER_IP}:{SERVER_PORT}  slot={SLOT}")

    # Wait for a joystick to become available.
    js = None
    while js is None:
        count = pygame.joystick.get_count()
        if count == 0:
            print("No joystick detected, waiting...")
        elif count <= JOYSTICK_INDEX:
            print(f"Joystick {JOYSTICK_INDEX} not found ({count} available), waiting...")
        else:
            try:
                js = pygame.joystick.Joystick(JOYSTICK_INDEX)
                js.init()
            except pygame.error as exc:
                print(f"Failed to init joystick: {exc}")
                js = None

        if js is None:
            time.sleep(1)
            pygame.joystick.quit()
            pygame.joystick.init()

    print(f"Using joystick: {js.get_name()}")
    print("Sending input. Press Ctrl+C to stop.")

    try:
        while True:
            t_start = time.monotonic()
            pygame.event.pump()

            # Read buttons (pad to 16 entries for consistent indexing).
            buttons = [js.get_button(i) for i in range(min(js.get_numbuttons(), 16))]
            while len(buttons) < 16:
                buttons.append(0)

            # Hat switches → D-pad booleans.
            hat_up = hat_right = hat_down = hat_left = False
            for hat_x, hat_y in (js.get_hat(i) for i in range(js.get_numhats())):
                hat_left  |= hat_x < 0
                hat_right |= hat_x > 0
                hat_up    |= hat_y > 0
                hat_down  |= hat_y < 0

            axes = [js.get_axis(i) for i in range(js.get_numaxes())]

            L_stick = (
                _axis_to_byte(axes[0]), _axis_to_byte(axes[1])
            ) if len(axes) >= 2 else (128, 128)

            R_stick = (
                _axis_to_byte(axes[2]), _axis_to_byte(axes[3])
            ) if len(axes) >= 4 else (128, 128)

            analog_L2 = _axis_to_byte(axes[4]) if len(axes) >= 5 else 0
            analog_R2 = _axis_to_byte(axes[5]) if len(axes) >= 6 else 0

            # Button index mapping mirrors demo/pygame_controller.py.
            buttons1 = _button_mask_1(
                share=bool(buttons[4]),
                l3=bool(buttons[7]),
                r3=bool(buttons[8]),
                options=bool(buttons[6]),
                up=bool(buttons[11]) or hat_up,
                right=bool(buttons[14]) or hat_right,
                down=bool(buttons[12]) or hat_down,
                left=bool(buttons[13]) or hat_left,
            )
            buttons2 = _button_mask_2(
                l2=analog_L2 > 0,
                r2=analog_R2 > 0,
                l1=bool(buttons[9]),
                r1=bool(buttons[10]),
                triangle=bool(buttons[3]),
                circle=bool(buttons[1]),
                cross=bool(buttons[0]),
                square=bool(buttons[2]),
            )

            pkt = _build_packet(
                slot=SLOT,
                packet_num=packet_num,
                buttons1=buttons1,
                buttons2=buttons2,
                home=bool(buttons[5]),
                touch_button=bool(buttons[15]),
                L_stick=L_stick,
                R_stick=R_stick,
                analog_L1=255 if buttons[9] else 0,
                analog_R1=255 if buttons[10] else 0,
                analog_L2=analog_L2,
                analog_R2=analog_R2,
            )

            sock.sendto(pkt, server_addr)
            packet_num = (packet_num + 1) & 0xFFFFFFFF

            elapsed = time.monotonic() - t_start
            remaining = send_interval - elapsed
            if remaining > 0:
                time.sleep(remaining)

    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        sock.close()
        pygame.quit()


if __name__ == "__main__":
    main()
