"""Standalone pygame-to-Sys-Botbase bridge.

This tool bypasses the DSU networking layer and forwards controller input read
directly from ``pygame`` to a Sys-Botbase endpoint. A small Tkinter GUI mirrors
the Sys-Botbase options from the viewer, allowing you to configure the target
IP, optional polling rate limit, anti-jitter smoothing, and deadzone.
"""

from __future__ import annotations

import logging
import threading
import time
from tkinter import BooleanVar, StringVar, Tk, messagebox, simpledialog, ttk

import pygame

from libraries.inputs import frame_delay
from tools.sys_botbase import SysBotbaseBridge

__all__ = ["main"]


def _axis_to_byte(value: float) -> int:
    """Convert a pygame axis value (-1.0..1.0) to an unsigned byte."""
    v = int((value + 1.0) * 127.5)
    return max(0, min(v, 255))


class _LocalClient:
    """Minimal client shim so ``SysBotbaseBridge`` can consume local state."""

    def __init__(self):
        self.state_callback = None

    def dispatch(self, slot: int, state: dict) -> None:
        if self.state_callback is None:
            return
        try:
            self.state_callback(slot, state)
        except Exception as exc:  # pragma: no cover - defensive
            logging.error("State callback failed: %s", exc)


class PygameControllerReader:
    """Poll controller state with pygame and forward to a state callback."""

    def __init__(self, client: _LocalClient):
        self.client = client
        self.slot = 0
        self.joystick_index = 0
        self.thread: threading.Thread | None = None
        self.stop_event: threading.Event | None = None

    def ensure_joystick(self, joystick_index: int) -> tuple[bool, str | None]:
        """Verify that the requested joystick is available."""
        pygame.init()
        pygame.joystick.init()
        try:
            count = pygame.joystick.get_count()
            if count == 0:
                return False, "No joysticks detected."
            if joystick_index >= count:
                return (
                    False,
                    f"Joystick {joystick_index} not available (found {count}).",
                )
            js = pygame.joystick.Joystick(joystick_index)
            try:
                js.init()
            except pygame.error as exc:
                return False, f"Failed to initialize joystick {joystick_index}: {exc}"
            finally:
                js.quit()
            return True, None
        finally:
            pygame.joystick.quit()
            pygame.quit()

    def start(self, slot: int, joystick_index: int) -> tuple[bool, str | None]:
        """Begin polling the joystick on a background thread."""
        self.stop()
        self.slot = slot
        self.joystick_index = joystick_index
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()
        return True, None

    def stop(self) -> None:
        if self.stop_event is not None:
            self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=0.5)
        self.thread = None
        self.stop_event = None

    def _loop(self) -> None:
        stop_event = self.stop_event
        if stop_event is None:
            return

        pygame.init()
        pygame.joystick.init()
        try:
            js = pygame.joystick.Joystick(self.joystick_index)
            js.init()
        except pygame.error as exc:
            logging.error("Failed to initialize joystick %d: %s", self.joystick_index, exc)
            return

        try:
            while not stop_event.is_set():
                pygame.event.pump()
                state = self._read_state(js)
                self.client.dispatch(self.slot, state)
                time.sleep(frame_delay)
        finally:
            try:
                js.quit()
            finally:
                pygame.joystick.quit()
                pygame.quit()

    def _read_state(self, js: pygame.joystick.Joystick) -> dict:
        buttons_raw = [js.get_button(i) for i in range(js.get_numbuttons())]
        axes = [js.get_axis(i) for i in range(js.get_numaxes())]
        hat_x = 0
        hat_y = 0
        if js.get_numhats() > 0:
            hat_x, hat_y = js.get_hat(0)

        def _btn(idx: int) -> bool:
            return idx < len(buttons_raw) and bool(buttons_raw[idx])

        ls_x = _axis_to_byte(axes[0]) if len(axes) >= 1 else 128
        ls_y = _axis_to_byte(axes[1]) if len(axes) >= 2 else 128
        rs_x = _axis_to_byte(axes[2]) if len(axes) >= 3 else 128
        rs_y = _axis_to_byte(axes[3]) if len(axes) >= 4 else 128

        analog_l2 = _axis_to_byte(axes[4]) if len(axes) >= 5 else 0
        analog_r2 = _axis_to_byte(axes[5]) if len(axes) >= 6 else 0

        dpad_up = hat_y > 0 or _btn(11)
        dpad_right = hat_x > 0 or _btn(14)
        dpad_down = hat_y < 0 or _btn(12)
        dpad_left = hat_x < 0 or _btn(13)

        buttons = {
            "A": _btn(0),
            "B": _btn(1),
            "X": _btn(2),
            "Y": _btn(3),
            "Share": _btn(4),
            "Options": _btn(6),
            "L3": _btn(7),
            "R3": _btn(8),
            "L1": _btn(9),
            "R1": _btn(10),
            "D-Pad Up": dpad_up,
            "D-Pad Right": dpad_right,
            "D-Pad Down": dpad_down,
            "D-Pad Left": dpad_left,
            "L2": analog_l2 > 0,
            "R2": analog_r2 > 0,
        }

        return {
            "buttons": buttons,
            "home": _btn(5),
            "ls": (ls_x, ls_y),
            "rs": (rs_x, rs_y),
        }


class SysBotDialog(simpledialog.Dialog):
    """Sys-Botbase configuration with pygame joystick selection."""

    def __init__(
        self,
        parent,
        initial_ip: str | None,
        initial_slot: int,
        initial_rate: float | None,
        initial_smoothing: bool,
        initial_deadzone: int | None,
        initial_joystick: int,
    ):
        self.initial_ip = initial_ip or ""
        self.initial_slot = initial_slot
        self.initial_rate = initial_rate
        self.initial_smoothing = initial_smoothing
        self.initial_deadzone = initial_deadzone
        self.initial_joystick = initial_joystick
        self.result: tuple[str, int, float | None, bool, int | None, int] | None = None
        super().__init__(parent, "Sys-Botbase")

    def body(self, master):
        ttk.Label(master, text="Sys-Botbase IP:").grid(row=0, column=0, sticky="w", padx=4, pady=4)
        self.ip_entry = ttk.Entry(master)
        self.ip_entry.insert(0, self.initial_ip)
        self.ip_entry.grid(row=0, column=1, sticky="ew", padx=4, pady=4)

        ttk.Label(master, text="Controller slot:").grid(row=1, column=0, sticky="w", padx=4, pady=4)
        self.slot_entry = ttk.Entry(master)
        self.slot_entry.insert(0, str(self.initial_slot))
        self.slot_entry.grid(row=1, column=1, sticky="ew", padx=4, pady=4)

        ttk.Label(master, text="Max packet rate (Hz, optional):").grid(
            row=2, column=0, sticky="w", padx=4, pady=4
        )
        self.rate_entry = ttk.Entry(master)
        if self.initial_rate:
            self.rate_entry.insert(0, str(self.initial_rate))
        self.rate_entry.grid(row=2, column=1, sticky="ew", padx=4, pady=4)

        self.smoothing_var = BooleanVar(value=self.initial_smoothing)
        ttk.Checkbutton(
            master,
            text="Anti-Jitter",
            variable=self.smoothing_var,
        ).grid(row=3, column=0, columnspan=2, sticky="w", padx=4, pady=4)

        ttk.Label(master, text="Deadzone (0-255, optional):").grid(
            row=4, column=0, sticky="w", padx=4, pady=4
        )
        self.deadzone_entry = ttk.Entry(master)
        if self.initial_deadzone is not None:
            self.deadzone_entry.insert(0, str(self.initial_deadzone))
        self.deadzone_entry.grid(row=4, column=1, sticky="ew", padx=4, pady=4)

        ttk.Label(master, text="Pygame joystick index:").grid(
            row=5, column=0, sticky="w", padx=4, pady=4
        )
        self.joystick_entry = ttk.Entry(master)
        self.joystick_entry.insert(0, str(self.initial_joystick))
        self.joystick_entry.grid(row=5, column=1, sticky="ew", padx=4, pady=4)

        master.columnconfigure(1, weight=1)
        return self.ip_entry

    def validate(self) -> bool:
        ip = self.ip_entry.get().strip()
        slot_raw = self.slot_entry.get().strip()
        rate_raw = self.rate_entry.get().strip()
        deadzone_raw = self.deadzone_entry.get().strip()
        joystick_raw = self.joystick_entry.get().strip()

        if not ip:
            messagebox.showerror("Sys-Botbase", "IP address is required.")
            return False
        try:
            slot = int(slot_raw)
        except ValueError:
            messagebox.showerror("Sys-Botbase", "Controller slot must be a number.")
            return False
        if slot < 0:
            messagebox.showerror("Sys-Botbase", "Controller slot cannot be negative.")
            return False

        rate = None
        if rate_raw:
            try:
                rate = float(rate_raw)
            except ValueError:
                messagebox.showerror("Sys-Botbase", "Polling rate must be a number.")
                return False
            if rate <= 0:
                messagebox.showerror("Sys-Botbase", "Polling rate must be positive.")
                return False

        deadzone = None
        if deadzone_raw:
            try:
                deadzone = int(deadzone_raw)
            except ValueError:
                messagebox.showerror("Sys-Botbase", "Deadzone must be a number.")
                return False
            if deadzone < 0:
                messagebox.showerror("Sys-Botbase", "Deadzone cannot be negative.")
                return False
            if deadzone == 0:
                deadzone = None

        try:
            joystick_index = int(joystick_raw)
        except ValueError:
            messagebox.showerror("Sys-Botbase", "Joystick index must be a number.")
            return False
        if joystick_index < 0:
            messagebox.showerror("Sys-Botbase", "Joystick index cannot be negative.")
            return False

        self._validated_ip = ip
        self._validated_slot = slot
        self._validated_rate = rate
        self._validated_smoothing = bool(self.smoothing_var.get())
        self._validated_deadzone = deadzone
        self._validated_joystick = joystick_index
        return True

    def apply(self):
        self.result = (
            self._validated_ip,
            self._validated_slot,
            self._validated_rate,
            self._validated_smoothing,
            self._validated_deadzone,
            self._validated_joystick,
        )


class StandaloneSysBotApp:
    """Tk UI for the pygame-to-Sys-Botbase bridge."""

    def __init__(self):
        self.client = _LocalClient()
        self.bridge = SysBotbaseBridge(self.client)
        self.reader = PygameControllerReader(self.client)

        self.root = Tk()
        self.root.title("Sys-Botbase Direct Bridge")

        self.status_var = StringVar(value="Disconnected")
        self.button = ttk.Button(self.root, text="Connect", command=self._toggle)
        self.button.pack(fill="x", padx=8, pady=(8, 4))
        ttk.Label(self.root, textvariable=self.status_var).pack(fill="x", padx=8, pady=(0, 8))

        self.ip = "127.0.0.1"
        self.slot = 0
        self.rate = None
        self.smoothing = False
        self.deadzone = None
        self.joystick_index = 0

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _toggle(self):
        if self.bridge.active:
            self._stop()
            return

        dialog = SysBotDialog(
            self.root,
            self.ip,
            self.slot,
            self.rate,
            self.smoothing,
            self.deadzone,
            self.joystick_index,
        )
        if dialog.result is None:
            return

        ip, slot, rate, smoothing, deadzone, joystick_index = dialog.result
        ok, err = self.reader.ensure_joystick(joystick_index)
        if not ok:
            messagebox.showerror("Sys-Botbase", err)
            return
        if not self.bridge.start(
            ip,
            slot,
            max_rate_hz=rate,
            smoothing=smoothing,
            deadzone=deadzone,
        ):
            messagebox.showerror("Sys-Botbase", "Failed to connect to sys-botbase server.")
            return

        started, err = self.reader.start(slot, joystick_index)
        if not started:
            self.bridge.stop()
            if err:
                messagebox.showerror("Sys-Botbase", err)
            return

        self.ip = ip
        self.slot = slot
        self.rate = rate
        self.smoothing = smoothing
        self.deadzone = deadzone
        self.joystick_index = joystick_index

        self.status_var.set(
            f"Connected to {ip} (slot {slot}, joystick {joystick_index}"
            f"{', max ' + str(rate) + ' Hz' if rate else ''})"
        )
        self.button.config(text="Disconnect")

    def _stop(self):
        self.reader.stop()
        self.bridge.stop()
        self.status_var.set("Disconnected")
        self.button.config(text="Connect")

    def _on_close(self):
        self._stop()
        self.root.destroy()

    def run(self):
        try:
            self.root.mainloop()
        finally:
            self._stop()


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    app = StandaloneSysBotApp()
    app.run()


if __name__ == "__main__":
    main()
