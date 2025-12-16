import logging
import socket
import threading
import time
from typing import Callable

__all__ = ["SysBotbaseBridge"]


def _scale_axis(value: int) -> int:
    """Convert 0-255 DSU axis value to sys-botbase range (-0x8000..0x7FFF)."""
    centered = value - 128
    if centered >= 0:
        scaled = int(centered / 127 * 0x7FFF)
    else:
        scaled = int(centered / 128 * 0x8000)
    return max(-0x8000, min(0x7FFF, scaled))


def _invert_axis(value: int) -> int:
    """Flip an already scaled axis value without exceeding valid bounds."""
    return max(-0x8000, min(0x7FFF, -value))


class SysBotbaseBridge:
    """Stream DSU controller state to a sys-botbase endpoint."""

    DEFAULT_PORT = 6000
    BUTTON_MAP = {
        # DSU uses Xbox-style face labels; sys-botbase expects Switch layout.
        "A": "B",
        "B": "A",
        "X": "Y",
        "Y": "X",
        "R1": "R",
        "L1": "L",
        "R2": "ZR",
        "L2": "ZL",
        "Options": "PLUS",
        "Share": "MINUS",
        "R3": "RSTICK",
        "L3": "LSTICK",
        "D-Pad Up": "DUP",
        "D-Pad Down": "DDOWN",
        "D-Pad Left": "DLEFT",
        "D-Pad Right": "DRIGHT",
    }

    def __init__(self, client):
        self.client = client
        self.target_ip: str | None = None
        self.slot: int | None = None
        self.sock: socket.socket | None = None
        self._last_buttons: set[str] = set()
        self._last_sticks: tuple[int, int, int, int] | None = None
        self._last_raw_sticks: tuple[int, int, int, int] | None = None
        self._prev_callback: Callable | None = None
        self._callback = None
        self._max_rate_hz: float | None = None
        self._poll_interval: float | None = None
        self._stop_event: threading.Event | None = None
        self._pending_event: threading.Event | None = None
        self._pending_sticks: tuple[int, int, int, int] | None = None
        self._pending_dirty: bool = False
        self._send_thread: threading.Thread | None = None
        self._smoothing_enabled = False
        self._deadzone: int | None = None

    @property
    def active(self) -> bool:
        return self.sock is not None

    def start(self, ip: str, slot: int, max_rate_hz: float | None = None,
              smoothing: bool = False, deadzone: int | None = None) -> bool:
        """Connect to sys-botbase at ``ip`` and forward updates from ``slot``.

        When ``max_rate_hz`` is provided, outgoing packets are throttled to the
        requested rate to avoid flooding sys-botbase with rapid stick updates.

        Manual repro: start the bridge with ``max_rate_hz`` set (for example
        30), press a button, and confirm the press is observed immediately
        while stick movements continue to follow the configured rate limit.
        """
        self.stop()
        try:
            sock = socket.create_connection((ip, self.DEFAULT_PORT), timeout=1.0)
            sock.settimeout(1.0)
        except OSError as exc:
            logging.error("Failed to connect to sys-botbase at %s:%d: %s",
                          ip, self.DEFAULT_PORT, exc)
            return False

        self.target_ip = ip
        self.slot = slot
        self.sock = sock
        self._last_buttons.clear()
        self._last_sticks = None
        self._last_raw_sticks = None
        self._prev_callback = self.client.state_callback
        self._max_rate_hz = max_rate_hz if max_rate_hz and max_rate_hz > 0 else None
        self._poll_interval = (1.0 / self._max_rate_hz) if self._max_rate_hz else None
        self._smoothing_enabled = smoothing
        self._deadzone = deadzone if deadzone and deadzone > 0 else None
        if self._max_rate_hz:
            self._stop_event = threading.Event()
            self._pending_event = threading.Event()
            self._pending_sticks = None
            self._pending_dirty = False
            self._send_thread = threading.Thread(target=self._send_loop, daemon=True)
            self._send_thread.start()

        def callback(slot_id: int, state: dict) -> None:
            if self._prev_callback is not None:
                try:
                    self._prev_callback(slot_id, state)
                except Exception as exc:  # pragma: no cover - defensive
                    logging.error("State callback failed: %s", exc)
            if slot_id != self.slot or not self.active:
                return
            mapped_buttons = self._map_buttons(state)
            self._dispatch_buttons(mapped_buttons)
            sticks = self._extract_sticks(state)
            if self._max_rate_hz:
                self._pending_sticks = sticks
                self._pending_dirty = True
                if self._pending_event is not None:
                    self._pending_event.set()
            else:
                self._dispatch_sticks(sticks)

        self._callback = callback
        self.client.state_callback = self._callback
        return True

    def stop(self) -> None:
        """Close the sys-botbase connection and restore callbacks."""
        if self._stop_event is not None:
            self._stop_event.set()
        if self._pending_event is not None:
            self._pending_event.set()
        if self._send_thread is not None and threading.current_thread() is not self._send_thread:
            self._send_thread.join(timeout=0.5)
        self._send_thread = None
        self._stop_event = None
        self._pending_event = None
        self._pending_sticks = None
        self._pending_dirty = False
        self._max_rate_hz = None
        self._poll_interval = None

        if self.sock is not None:
            try:
                self._send_neutral_state()
            except OSError as exc:
                logging.warning("Failed to send neutral state to sys-botbase: %s", exc)
            try:
                self._send_command("detachController")
            except OSError as exc:
                logging.warning("Failed to detach controller from sys-botbase: %s", exc)
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = None
        self.target_ip = None
        self.slot = None
        self._last_buttons.clear()
        self._last_sticks = None
        self._last_raw_sticks = None
        self._smoothing_enabled = False
        self._deadzone = None
        if self.client.state_callback is self._callback:
            self.client.state_callback = self._prev_callback
        self._prev_callback = None
        self._callback = None

    def _dispatch_buttons(self, pressed: set[str]) -> None:
        try:
            self._sync_buttons(pressed)
        except OSError as exc:
            logging.error("Failed to forward buttons to sys-botbase at %s: %s",
                          self.target_ip, exc)
            self.stop()

    def _dispatch_sticks(self, sticks: tuple[int, int, int, int]) -> None:
        try:
            self._sync_sticks(sticks)
        except OSError as exc:
            logging.error("Failed to forward sticks to sys-botbase at %s: %s",
                          self.target_ip, exc)
            self.stop()

    def _send_loop(self) -> None:
        stop_event = self._stop_event
        pending_event = self._pending_event
        if self._poll_interval is None or stop_event is None or pending_event is None:
            return
        next_send = time.monotonic()
        while not stop_event.is_set():
            wait_time = max(0.0, next_send - time.monotonic())
            pending_event.wait(wait_time)
            pending_event.clear()
            if stop_event.is_set():
                break
            now = time.monotonic()
            if now < next_send:
                continue
            if not self._pending_dirty or self._pending_sticks is None:
                next_send = now + self._poll_interval
                continue
            self._dispatch_sticks(self._pending_sticks)
            self._pending_dirty = False
            next_send = now + self._poll_interval

    def _map_buttons(self, state: dict) -> set[str]:
        """Translate DSU button names to sys-botbase labels."""
        mapped = set()
        for name, pressed in state.get("buttons", {}).items():
            if pressed and name in self.BUTTON_MAP:
                mapped.add(self.BUTTON_MAP[name])
        if state.get("home"):
            mapped.add("HOME")
        return mapped

    def _sync_buttons(self, pressed: set[str]) -> None:
        to_release = self._last_buttons - pressed
        to_press = pressed - self._last_buttons
        for btn in sorted(to_release):
            self._send_command(f"release {btn}")
        for btn in sorted(to_press):
            self._send_command(f"press {btn}")
        self._last_buttons = pressed

    def _extract_sticks(self, state: dict) -> tuple[int, int, int, int]:
        ls_x, ls_y = state.get("ls", (128, 128))
        rs_x, rs_y = state.get("rs", (128, 128))
        ls_x = self._apply_deadzone(ls_x)
        ls_y = self._apply_deadzone(ls_y)
        rs_x = self._apply_deadzone(rs_x)
        rs_y = self._apply_deadzone(rs_y)
        return ls_x, ls_y, rs_x, rs_y

    def _sync_sticks(self, raw_sticks: tuple[int, int, int, int]) -> None:
        if self._smoothing_enabled and self._last_raw_sticks is not None:
            deltas = [abs(a - b) for a, b in zip(raw_sticks, self._last_raw_sticks)]
            if max(deltas) < 3:
                return
        left = (_scale_axis(raw_sticks[0]), _invert_axis(_scale_axis(raw_sticks[1])))
        right = (_scale_axis(raw_sticks[2]), _invert_axis(_scale_axis(raw_sticks[3])))
        sticks = (*left, *right)
        if self._last_sticks is None or sticks[:2] != self._last_sticks[:2]:
            self._send_command(f"setStick LEFT {left[0]} {left[1]}")
        if self._last_sticks is None or sticks[2:] != self._last_sticks[2:]:
            self._send_command(f"setStick RIGHT {right[0]} {right[1]}")
        self._last_sticks = sticks
        self._last_raw_sticks = raw_sticks

    def _send_neutral_state(self) -> None:
        """Release any held inputs and recenter sticks."""
        if self._last_buttons:
            for btn in sorted(self._last_buttons):
                self._send_command(f"release {btn}")
        self._send_command("setStick LEFT 0 0")
        self._send_command("setStick RIGHT 0 0")

    def _send_command(self, command: str) -> None:
        if self.sock is None:
            return
        self.sock.sendall((command + "\r\n").encode("ascii"))

    def _apply_deadzone(self, value: int) -> int:
        """Clamp small stick motions to center when deadzone is set."""
        if self._deadzone is None:
            return value
        if abs(value - 128) <= self._deadzone:
            return 128
        return value
