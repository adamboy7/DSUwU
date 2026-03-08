"""Standalone pygame → sys-botbase controller bridge.

Reads a physical gamepad via pygame and forwards input directly to a Nintendo
Switch running sys-botbase over TCP, bypassing the DSUwU server entirely.

Configuration constants (edit as needed):
- ``JOYSTICK_INDEX``: Pygame joystick index (0 = first controller).
- ``BOTBASE_PORT``: TCP port sys-botbase listens on (default 6000).
- ``TRIGGER_THRESHOLD``: Axis value above which L2/R2 count as pressed (0.0–1.0).
- ``STICK_DEADZONE``: Axis magnitude below which sticks are treated as centred.
- ``INVERT_STICK_X``: Set True if stick X appears inverted in-game.
- ``INVERT_STICK_Y``: Set True if stick Y appears inverted in-game.
- ``SWAP_ABXY``: Set True to swap A↔B and X↔Y (PS4 positional → Switch layout).

Last-used IP is saved to ``sys_botbase_last.json`` next to this script.

Button layout assumes a DualShock 4 / DualSense controller via pygame.
Adjust ``_BUTTON_MAP`` if your gamepad reports different indices.
"""

import json
import os
import socket
import sys
import time

try:
    import pygame
except ImportError:
    sys.exit("pygame is required: pip install pygame")


# ── configuration ─────────────────────────────────────────────────────────────
JOYSTICK_INDEX    = 0
BOTBASE_PORT      = 6000
TRIGGER_THRESHOLD = 0.2    # 0.0–1.0; above this = ZL/ZR pressed
STICK_DEADZONE    = 0.05   # axis magnitude; smaller treated as centred
INVERT_STICK_X    = False
INVERT_STICK_Y    = True
SWAP_ABXY         = True         # swap A↔B and X↔Y (PS4 positional → Switch layout)

_ABXY_SWAP = {"A": "B", "B": "A", "X": "Y", "Y": "X"}

_SAVE_FILE = os.path.join(os.path.dirname(__file__), "sys_botbase_last.json")

# ── axis helpers ──────────────────────────────────────────────────────────────
def _axis_to_sb(value: float) -> int:
    """Convert pygame axis (-1.0..1.0) to sys-botbase range (-0x8000..0x7FFF)."""
    if value >= 0.0:
        return min(0x7FFF, int(value * 0x7FFF))
    return max(-0x8000, int(value * 0x8000))


def _dead(value: float) -> float:
    """Apply deadzone: return 0.0 if magnitude is below threshold."""
    return 0.0 if abs(value) < STICK_DEADZONE else value


# ── button map ────────────────────────────────────────────────────────────────
# Maps pygame button index → sys-botbase button name.
# Assumes DualShock 4 / DualSense layout as reported by pygame on Windows/Linux.
# Adjust if your controller reports different indices.
_BUTTON_MAP = {
    0:  "A",       # cross    (bottom)
    1:  "B",       # circle   (right)
    2:  "X",       # square   (left)
    3:  "Y",       # triangle (top)
    4:  "MINUS",   # share / select
    5:  "HOME",    # PS / home
    6:  "PLUS",    # options / start
    7:  "LSTICK",  # L3
    8:  "RSTICK",  # R3
    9:  "L",       # L1
    10: "R",       # R1
    # buttons 11–14 are d-pad on some drivers; also covered by hat fallback below
    11: "DUP",
    12: "DDOWN",
    13: "DLEFT",
    14: "DRIGHT",
}


# ── save / load ───────────────────────────────────────────────────────────────
def _load_last() -> str:
    try:
        with open(_SAVE_FILE) as f:
            data = json.load(f)
        return str(data.get("ip", ""))
    except (OSError, ValueError):
        return ""


def _save_last(ip: str) -> None:
    try:
        with open(_SAVE_FILE, "w") as f:
            json.dump({"ip": ip}, f)
    except OSError:
        pass


# ── TCP helpers ───────────────────────────────────────────────────────────────
def _connect(ip: str) -> socket.socket | None:
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
        print(f"Connection failed: {exc}", file=sys.stderr, flush=True)
        return None


def _send(sock: socket.socket, cmd: str) -> bool:
    try:
        sock.sendall((cmd + "\r\n").encode())
        return True
    except OSError as exc:
        print(f"Send error: {exc}", file=sys.stderr, flush=True)
        return False


def _cleanup(sock: socket.socket, held: set[str]) -> None:
    for btn in list(held):
        _send(sock, f"release {btn}")
    held.clear()
    _send(sock, "setStick LEFT 0 0")
    _send(sock, "setStick RIGHT 0 0")


# ── joystick init ─────────────────────────────────────────────────────────────
def _init_joystick() -> "pygame.joystick.JoystickType | None":
    pygame.joystick.quit()
    pygame.joystick.init()
    count = pygame.joystick.get_count()
    if count == 0:
        print("No joystick detected.", file=sys.stderr)
        return None
    if count <= JOYSTICK_INDEX:
        print(f"Joystick {JOYSTICK_INDEX} not available ({count} found).", file=sys.stderr)
        return None
    js = pygame.joystick.Joystick(JOYSTICK_INDEX)
    try:
        js.init()
        print(f"Using joystick {JOYSTICK_INDEX}: {js.get_name()}", flush=True)
        return js
    except pygame.error as exc:
        print(f"Failed to init joystick: {exc}", file=sys.stderr)
        return None


# ── main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    last_ip = _load_last()
    ip_prompt = f"Switch IP address [{last_ip}]: " if last_ip else "Switch IP address: "
    ip_input = input(ip_prompt).strip()
    ip = ip_input if ip_input else last_ip
    if not ip:
        sys.exit("No IP address provided.")
    _save_last(ip)

    pygame.init()
    js = None
    while js is None:
        js = _init_joystick()
        if js is None:
            print("Retrying in 2 seconds…")
            time.sleep(2)

    sock = _connect(ip)
    while sock is None:
        print("Retrying connection in 2 seconds…")
        time.sleep(2)
        sock = _connect(ip)

    held: set[str] = set()
    prev_buttons: set[str] = set()
    prev_left_stick: tuple[int, int] = (0, 0)
    prev_right_stick: tuple[int, int] = (0, 0)

    POLL_DELAY = 1 / 240.0  # ~240 Hz polling for low latency

    print("Forwarding input. Press Ctrl+C to exit.", flush=True)
    try:
        while True:
            pygame.event.pump()

            # ── buttons ───────────────────────────────────────────────────
            n_buttons = min(js.get_numbuttons(), max(_BUTTON_MAP.keys()) + 1)
            cur_buttons: set[str] = set()
            for idx in range(n_buttons):
                if js.get_button(idx) and idx in _BUTTON_MAP:
                    name = _BUTTON_MAP[idx]
                    cur_buttons.add(_ABXY_SWAP.get(name, name) if SWAP_ABXY else name)

            # Hat d-pad (overrides/supplements button indices 11–14)
            for i in range(js.get_numhats()):
                hx, hy = js.get_hat(i)
                if hx < 0:
                    cur_buttons.discard("DRIGHT"); cur_buttons.add("DLEFT")
                elif hx > 0:
                    cur_buttons.discard("DLEFT"); cur_buttons.add("DRIGHT")
                if hy > 0:
                    cur_buttons.discard("DDOWN"); cur_buttons.add("DUP")
                elif hy < 0:
                    cur_buttons.discard("DUP"); cur_buttons.add("DDOWN")

            # ── triggers (digital) ────────────────────────────────────────
            axes = [js.get_axis(i) for i in range(js.get_numaxes())]
            # Triggers typically report -1.0 (released) to 1.0 (fully pressed).
            l2_raw = axes[4] if len(axes) > 4 else -1.0
            r2_raw = axes[5] if len(axes) > 5 else -1.0
            if (l2_raw + 1.0) / 2.0 > TRIGGER_THRESHOLD:
                cur_buttons.add("ZL")
            if (r2_raw + 1.0) / 2.0 > TRIGGER_THRESHOLD:
                cur_buttons.add("ZR")

            # ── analog sticks ─────────────────────────────────────────────
            lx = _dead(axes[0] if len(axes) > 0 else 0.0)
            ly = _dead(axes[1] if len(axes) > 1 else 0.0)
            rx = _dead(axes[2] if len(axes) > 2 else 0.0)
            ry = _dead(axes[3] if len(axes) > 3 else 0.0)

            sb_lx = _axis_to_sb(-lx if INVERT_STICK_X else lx)
            sb_ly = _axis_to_sb(-ly if INVERT_STICK_Y else ly)
            sb_rx = _axis_to_sb(-rx if INVERT_STICK_X else rx)
            sb_ry = _axis_to_sb(-ry if INVERT_STICK_Y else ry)

            cur_left_stick  = (sb_lx, sb_ly)
            cur_right_stick = (sb_rx, sb_ry)

            ok = True

            # ── send button changes ───────────────────────────────────────
            for btn in prev_buttons - cur_buttons:
                ok = ok and _send(sock, f"release {btn}")
                held.discard(btn)
            for btn in cur_buttons - prev_buttons:
                ok = ok and _send(sock, f"press {btn}")
                held.add(btn)

            # ── send stick changes ────────────────────────────────────────
            if cur_left_stick != prev_left_stick:
                ok = ok and _send(sock, f"setStick LEFT {cur_left_stick[0]} {cur_left_stick[1]}")
            if cur_right_stick != prev_right_stick:
                ok = ok and _send(sock, f"setStick RIGHT {cur_right_stick[0]} {cur_right_stick[1]}")

            if not ok:
                print("Connection lost. Reconnecting…", file=sys.stderr, flush=True)
                try:
                    sock.close()
                except OSError:
                    pass
                sock = None
                held.clear()
                prev_buttons.clear()
                prev_left_stick = (0, 0)
                prev_right_stick = (0, 0)
                while sock is None:
                    time.sleep(2)
                    sock = _connect(ip)
                continue

            prev_buttons     = cur_buttons
            prev_left_stick  = cur_left_stick
            prev_right_stick = cur_right_stick

            time.sleep(POLL_DELAY)

    except KeyboardInterrupt:
        print("\nExiting…", flush=True)
    finally:
        if sock is not None:
            try:
                _cleanup(sock, held)
            except OSError:
                pass
            sock.close()
        js.quit()
        pygame.quit()


if __name__ == "__main__":
    main()
