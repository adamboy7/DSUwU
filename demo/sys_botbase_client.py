"""Forward a DSUwU controller slot to a sys-botbase Switch server.

Prompts for the DSUwU source slot and the Switch IP address at startup,
then translates button/stick state into sys-botbase TCP commands each frame.
The last-used slot and IP are saved to ``sys_botbase_last.json`` and
pre-filled on the next run.

Configuration constants (edit as needed):
- ``BOTBASE_PORT``: TCP port sys-botbase listens on (default 6000).
- ``TRIGGER_THRESHOLD``: Analog value above which L2/R2 count as pressed (0–255).
- ``STICK_DEADZONE``: Minimum raw-unit change before a setStick is sent.
- ``INVERT_STICK_X``: Set True if stick X appears inverted in-game.
- ``INVERT_STICK_Y``: Set True if stick Y appears inverted in-game.
- ``SWAP_ABXY``: Set True to swap A↔B and X↔Y (PS4 positional → Switch layout).
"""

import json
import os
import socket
import sys
import time

from libraries.inputs import frame_delay


BOTBASE_PORT = 6000
TRIGGER_THRESHOLD = 50   # 0–255; above this = ZL/ZR pressed
STICK_DEADZONE = 2       # raw DSU units; smaller changes are ignored
INVERT_STICK_X = False   # flip X on both sticks if the game reads them inverted
INVERT_STICK_Y = True    # flip Y on both sticks if the game reads them inverted
SWAP_ABXY = True         # swap A↔B and X↔Y (PS4 positional → Switch layout)

_ABXY_SWAP = {"A": "B", "B": "A", "X": "Y", "Y": "X"}

_SAVE_FILE = os.path.join(os.path.dirname(__file__), "sys_botbase_last.json")

# Sys-botbase accepts –0x8000 to 0x7FFF; DSU uses 0–255 centred at 128.
_STICK_SCALE = 0x7FFF / 127  # ≈ 258.25


# ── button map ────────────────────────────────────────────────────────────────
# Each entry: (attribute_name, bitmask, sys-botbase_button_name)
# "home" and the two triggers are handled separately below.
_BUTTON_MAP = [
    # buttons2
    ("buttons2", 0x40, "A"),       # cross
    ("buttons2", 0x20, "B"),       # circle
    ("buttons2", 0x80, "X"),       # square
    ("buttons2", 0x10, "Y"),       # triangle
    ("buttons2", 0x04, "L"),       # l1
    ("buttons2", 0x08, "R"),       # r1
    # buttons1 – d-pad
    ("buttons1", 0x10, "DUP"),
    ("buttons1", 0x40, "DDOWN"),
    ("buttons1", 0x80, "DLEFT"),
    ("buttons1", 0x20, "DRIGHT"),
    # buttons1 – misc
    ("buttons1", 0x01, "MINUS"),   # share
    ("buttons1", 0x08, "PLUS"),    # options
    ("buttons1", 0x02, "LSTICK"),  # l3
    ("buttons1", 0x04, "RSTICK"),  # r3
]


def _dsu_to_sb(v: int) -> int:
    """Convert a DSU axis value (0–255, neutral 128) to sys-botbase range."""
    return max(-0x8000, min(0x7FFF, round((v - 128) * _STICK_SCALE)))


def _sb_hex(v: int) -> str:
    """Format a signed stick value for sys-botbase (decimal; strtol handles negatives)."""
    return str(v)


def _load_last() -> tuple[str, str]:
    """Return (slot_str, ip_str) from the save file, or empty strings."""
    try:
        with open(_SAVE_FILE) as f:
            data = json.load(f)
        return str(data.get("slot", "")), str(data.get("ip", ""))
    except (OSError, ValueError, KeyError):
        return "", ""


def _save_last(slot: int, ip: str) -> None:
    """Persist slot and IP for the next run."""
    try:
        with open(_SAVE_FILE, "w") as f:
            json.dump({"slot": slot, "ip": ip}, f)
    except OSError:
        pass


def controller_loop(stop_event, controller_states, slot):
    """Read a DSUwU slot and forward its state to a sys-botbase server."""

    last_slot, last_ip = _load_last()

    slot_prompt = f"DSUwU slot to mirror [{last_slot}]: " if last_slot else "DSUwU slot to mirror (e.g. 0): "
    slot_input = input(slot_prompt).strip()
    source_slot = int(slot_input) if slot_input else int(last_slot)

    ip_prompt = f"Switch IP address [{last_ip}]: " if last_ip else "Switch IP address: "
    ip_input = input(ip_prompt).strip()
    ip = ip_input if ip_input else last_ip

    _save_last(source_slot, ip)

    # Track previously-sent state so we only send changes.
    prev_buttons1: int = 0
    prev_buttons2: int = 0
    prev_home: bool = False
    prev_zl: bool = False
    prev_zr: bool = False
    prev_left_stick: tuple[int, int] = (128, 128)
    prev_right_stick: tuple[int, int] = (128, 128)

    # Buttons currently held (so we can release them all on exit).
    held: set[str] = set()

    sock: socket.socket | None = None

    def connect() -> socket.socket | None:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(3.0)
            s.connect((ip, BOTBASE_PORT))
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            s.settimeout(1.0)
            s.sendall(b"configure mainLoopSleepTime 0\r\n")
            print(f"Connected to sys-botbase at {ip}:{BOTBASE_PORT}", flush=True)
            return s
        except OSError as exc:
            print(f"sys-botbase connection failed: {exc}", file=sys.stderr, flush=True)
            return None

    def send(s: socket.socket, cmd: str) -> bool:
        """Send a newline-terminated command. Returns False on error."""
        try:
            s.sendall((cmd + "\r\n").encode())
            return True
        except OSError as exc:
            print(f"Send error: {exc}", file=sys.stderr, flush=True)
            return False

    def cleanup(s: socket.socket) -> None:
        """Release all held buttons and reset sticks before disconnecting."""
        for btn in list(held):
            send(s, f"release {btn}")
        held.clear()
        send(s, "setStick LEFT 0x0 0x0")
        send(s, "setStick RIGHT 0x0 0x0")

    sock = connect()

    # Access the dirty event once; it fires immediately on any state write.
    dirty = controller_states[source_slot]._dirty_event

    while not stop_event.is_set():
        if dirty is not None:
            dirty.wait(timeout=frame_delay)
            dirty.clear()
        else:
            time.sleep(frame_delay)

        if sock is None:
            time.sleep(2.0)
            sock = connect()
            if sock is None:
                continue
            # Reset tracking so all state is re-sent after reconnect.
            prev_buttons1 = 0
            prev_buttons2 = 0
            prev_home = False
            prev_zl = False
            prev_zr = False
            prev_left_stick = (128, 128)
            prev_right_stick = (128, 128)
            held.clear()

        state = controller_states[source_slot]

        cur_buttons1: int = state.buttons1
        cur_buttons2: int = state.buttons2
        cur_home: bool = bool(state.home)
        cur_zl: bool = state.analog_L2 > TRIGGER_THRESHOLD
        cur_zr: bool = state.analog_R2 > TRIGGER_THRESHOLD
        cur_left_stick: tuple[int, int] = tuple(state.L_stick)
        cur_right_stick: tuple[int, int] = tuple(state.R_stick)

        ok = True

        # ── regular buttons ───────────────────────────────────────────────
        for attr, mask, name in _BUTTON_MAP:
            cur_pressed = bool((cur_buttons2 if attr == "buttons2" else cur_buttons1) & mask)
            prv_pressed = bool((prev_buttons2 if attr == "buttons2" else prev_buttons1) & mask)
            if cur_pressed == prv_pressed:
                continue
            btn = _ABXY_SWAP.get(name, name) if SWAP_ABXY else name
            if cur_pressed:
                ok = ok and send(sock, f"press {btn}")
                held.add(btn)
            else:
                ok = ok and send(sock, f"release {btn}")
                held.discard(btn)

        # ── home ──────────────────────────────────────────────────────────
        if cur_home != prev_home:
            if cur_home:
                ok = ok and send(sock, "press HOME")
                held.add("HOME")
            else:
                ok = ok and send(sock, "release HOME")
                held.discard("HOME")

        # ── triggers (digital) ────────────────────────────────────────────
        if cur_zl != prev_zl:
            if cur_zl:
                ok = ok and send(sock, "press ZL")
                held.add("ZL")
            else:
                ok = ok and send(sock, "release ZL")
                held.discard("ZL")

        if cur_zr != prev_zr:
            if cur_zr:
                ok = ok and send(sock, "press ZR")
                held.add("ZR")
            else:
                ok = ok and send(sock, "release ZR")
                held.discard("ZR")

        # ── analog sticks ─────────────────────────────────────────────────
        lx, ly = cur_left_stick
        plx, ply = prev_left_stick
        if abs(lx - plx) > STICK_DEADZONE or abs(ly - ply) > STICK_DEADZONE:
            sb_lx = _dsu_to_sb(lx) * (-1 if INVERT_STICK_X else 1)
            sb_ly = _dsu_to_sb(ly) * (-1 if INVERT_STICK_Y else 1)
            ok = ok and send(sock, f"setStick LEFT {_sb_hex(sb_lx)} {_sb_hex(sb_ly)}")

        rx, ry = cur_right_stick
        prx, pry = prev_right_stick
        if abs(rx - prx) > STICK_DEADZONE or abs(ry - pry) > STICK_DEADZONE:
            sb_rx = _dsu_to_sb(rx) * (-1 if INVERT_STICK_X else 1)
            sb_ry = _dsu_to_sb(ry) * (-1 if INVERT_STICK_Y else 1)
            ok = ok and send(sock, f"setStick RIGHT {_sb_hex(sb_rx)} {_sb_hex(sb_ry)}")

        if not ok:
            # Connection lost; clean up and attempt reconnect next iteration.
            try:
                cleanup(sock)
            except OSError:
                pass
            sock.close()
            sock = None
            continue

        prev_buttons1 = cur_buttons1
        prev_buttons2 = cur_buttons2
        prev_home = cur_home
        prev_zl = cur_zl
        prev_zr = cur_zr
        prev_left_stick = cur_left_stick
        prev_right_stick = cur_right_stick

    # ── teardown ──────────────────────────────────────────────────────────
    if sock is not None:
        try:
            cleanup(sock)
        except OSError:
            pass
        sock.close()
