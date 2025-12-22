"""Standalone hidapi-to-Sys-Botbase bridge with touch support.

This variant mirrors ``tools/Sys-Botbase Standalone.py`` but pulls input from
HID reports (DualShock 4/DualSense family) so touchpad events can be forwarded
to sys-botbase alongside buttons and sticks.
"""

from __future__ import annotations

import importlib
import logging
import sys
import threading
import time
from pathlib import Path
from tkinter import BooleanVar, StringVar, Tk, messagebox, simpledialog, ttk
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from tools.sys_botbase import SysBotbaseBridge

SUPPORTED_CONTROLLERS: dict[tuple[int, int], str] = {
    (0x054C, 0x05C4): "DualShock 4",
    (0x054C, 0x09CC): "DualShock 4 (v2)",
    (0x054C, 0x0CE6): "DualSense",
}

__all__ = ["main"]


def _axis_to_byte(value: int) -> int:
    return max(0, min(value, 255))


def _touch(report: Iterable[int], start: int):
    touch_id = report[start]
    active = (touch_id & 0x80) == 0
    x = ((report[start + 2] & 0x0F) << 8) | report[start + 1]
    y = (report[start + 3] << 4) | (report[start + 2] >> 4)
    return {"active": active, "pos": (x, y)}


def _connection_from_report(report: list[int]) -> int:
    if report and report[0] in (0x11, 0x15):
        return 2
    if report and report[0] in (0x01, 0x05):
        return 0
    return 0


class _LocalClient:
    def __init__(self):
        self.state_callback = None

    def dispatch(self, slot: int, state: dict) -> None:
        if self.state_callback is None:
            return
        try:
            self.state_callback(slot, state)
        except Exception as exc:  # pragma: no cover - defensive
            logging.error("State callback failed: %s", exc)


class HIDControllerReader:
    """Stream controller reports via hidapi and forward state changes."""

    def __init__(self, client: _LocalClient):
        self.client = client
        self.slot = 0
        self.thread: threading.Thread | None = None
        self.stop_event: threading.Event | None = None
        self.hid_module = None

    def start(self, slot: int) -> tuple[bool, str | None]:
        self.stop()
        self.slot = slot
        self.hid_module = self._load_hid_module()
        if self.hid_module is None:
            return False, "hidapi is required (pip install hidapi)."
        if not self._has_supported_device():
            return False, "No supported HID controllers detected."
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

    def _load_hid_module(self):
        spec = importlib.util.find_spec("hid")
        if spec is None:
            return None
        return importlib.import_module("hid")

    def _has_supported_device(self) -> bool:
        if self.hid_module is None:
            return False
        for info in self.hid_module.enumerate():
            key = (info.get("vendor_id"), info.get("product_id"))
            if key in SUPPORTED_CONTROLLERS:
                return True
        return False

    def _open_controller(self):
        device_cls = getattr(self.hid_module, "Device", None) or getattr(self.hid_module, "device", None)
        if device_cls is None:
            return None, None
        for info in self.hid_module.enumerate():
            key = (info.get("vendor_id"), info.get("product_id"))
            if key not in SUPPORTED_CONTROLLERS:
                continue
            device_path = info.get("path")
            try:
                try:
                    device = device_cls(path=device_path)
                except TypeError:
                    device = device_cls()
                if not self._ensure_handle_open(device, info, device_path):
                    try:
                        device.close()
                    except Exception:
                        pass
                    continue
            except OSError as exc:
                name = SUPPORTED_CONTROLLERS.get(key, "unknown controller")
                logging.error("Failed to open %s: %s", name, exc)
                try:
                    device.close()
                except Exception:
                    pass
                continue
            return device, info
        return None, None

    def _ensure_handle_open(self, device, info, device_path) -> bool:
        vid = info.get("vendor_id")
        pid = info.get("product_id")
        serial = info.get("serial_number")

        if hasattr(device, "open_path") and device_path is not None:
            try:
                device.open_path(device_path)
                return True
            except Exception as exc:  # pragma: no cover - runtime dependent
                logging.warning("open_path failed: %s", exc)

        if hasattr(device, "open"):
            try:
                device.open(vid, pid, serial=serial) if serial is not None else device.open(vid, pid)
                return True
            except Exception as exc:  # pragma: no cover - runtime dependent
                logging.warning("device.open failed: %s", exc)

        return False

    def _loop(self) -> None:
        stop_event = self.stop_event
        if stop_event is None or self.hid_module is None:
            return

        device = None
        device_info = None
        last_state: dict | None = None
        read_timeout_ms = 4

        while not stop_event.is_set():
            if device is None:
                device, device_info = self._open_controller()
                if device is None:
                    time.sleep(1)
                    continue

            try:
                report = device.read(78, timeout_ms=read_timeout_ms)
            except OSError as exc:
                logging.warning("Lost HID device: %s", exc)
                try:
                    device.close()
                except Exception:
                    pass
                device = None
                time.sleep(1)
                continue
            except ValueError:
                if not self._ensure_handle_open(device, device_info or {}, device_info.get("path") if device_info else None):
                    try:
                        device.close()
                    except Exception:
                        pass
                    device = None
                    time.sleep(1)
                    continue
                try:
                    report = device.read(78, timeout_ms=read_timeout_ms)
                except Exception as exc:  # pragma: no cover - runtime dependent
                    logging.error("Failed to read after reopen: %s", exc)
                    try:
                        device.close()
                    except Exception:
                        pass
                    device = None
                    time.sleep(1)
                    continue

            if not report:
                continue

            base = _connection_from_report(report)
            min_length = base + 43
            if len(report) < min_length:
                continue

            face_byte = report[base + 5]
            shoulder_byte = report[base + 6]
            misc_byte = report[base + 7]

            buttons = self._button_states(face_byte, shoulder_byte)
            l2_analog = _axis_to_byte(report[base + 8])
            r2_analog = _axis_to_byte(report[base + 9])
            touch1 = _touch(report, base + 35)

            state = {
                "buttons": {
                    "A": buttons["cross"],
                    "B": buttons["circle"],
                    "X": buttons["square"],
                    "Y": buttons["triangle"],
                    "Share": buttons["share"],
                    "Options": buttons["options"],
                    "L3": buttons["l3"],
                    "R3": buttons["r3"],
                    "L1": buttons["l1"],
                    "R1": buttons["r1"],
                    "D-Pad Up": buttons["up"],
                    "D-Pad Right": buttons["right"],
                    "D-Pad Down": buttons["down"],
                    "D-Pad Left": buttons["left"],
                    "L2": l2_analog > 0,
                    "R2": r2_analog > 0,
                },
                "home": bool(misc_byte & 0x01),
                "ls": (_axis_to_byte(report[base + 1]), _axis_to_byte(report[base + 2])),
                "rs": (_axis_to_byte(report[base + 3]), _axis_to_byte(report[base + 4])),
                "touch1": touch1,
            }

            if state != last_state:
                self.client.dispatch(self.slot, state)
                last_state = state

        if device is not None:
            try:
                device.close()
            except Exception:
                pass

    def _button_states(self, face: int, shoulders: int) -> dict[str, bool]:
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


class HIDSysBotDialog(simpledialog.Dialog):
    """Sys-Botbase configuration for the HID bridge."""

    def __init__(
        self,
        parent,
        initial_ip: str | None,
        initial_slot: int,
        initial_rate: float | None,
        initial_smoothing: bool,
        initial_deadzone: int | None,
    ):
        self.initial_ip = initial_ip or ""
        self.initial_slot = initial_slot
        self.initial_rate = initial_rate
        self.initial_smoothing = initial_smoothing
        self.initial_deadzone = initial_deadzone
        self.result: tuple[str, int, float | None, bool, int | None] | None = None
        super().__init__(parent, "Sys-Botbase HID")

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

        master.columnconfigure(1, weight=1)
        return self.ip_entry

    def validate(self) -> bool:
        ip = self.ip_entry.get().strip()
        slot_raw = self.slot_entry.get().strip()
        rate_raw = self.rate_entry.get().strip()
        deadzone_raw = self.deadzone_entry.get().strip()

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

        self._validated_ip = ip
        self._validated_slot = slot
        self._validated_rate = rate
        self._validated_smoothing = bool(self.smoothing_var.get())
        self._validated_deadzone = deadzone
        return True

    def apply(self):
        self.result = (
            self._validated_ip,
            self._validated_slot,
            self._validated_rate,
            self._validated_smoothing,
            self._validated_deadzone,
        )


class HIDStandaloneSysBotApp:
    """Tk UI for the HID-to-Sys-Botbase bridge."""

    def __init__(self):
        self.client = _LocalClient()
        self.bridge = SysBotbaseBridge(self.client)
        self.reader = HIDControllerReader(self.client)

        self.root = Tk()
        self.root.title("Sys-Botbase HID Bridge")

        self.status_var = StringVar(value="Disconnected")
        self.button = ttk.Button(self.root, text="Connect", command=self._toggle)
        self.button.pack(fill="x", padx=8, pady=(8, 4))
        ttk.Label(self.root, textvariable=self.status_var).pack(fill="x", padx=8, pady=(0, 8))

        self.ip = "127.0.0.1"
        self.slot = 0
        self.rate = None
        self.smoothing = False
        self.deadzone = None

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _toggle(self):
        if self.bridge.active:
            self._stop()
            return

        dialog = HIDSysBotDialog(
            self.root,
            self.ip,
            self.slot,
            self.rate,
            self.smoothing,
            self.deadzone,
        )
        if dialog.result is None:
            return

        ip, slot, rate, smoothing, deadzone = dialog.result
        ok, err = self.reader.start(slot)
        if not ok:
            if err:
                messagebox.showerror("Sys-Botbase", err)
            return
        if not self.bridge.start(
            ip,
            slot,
            max_rate_hz=rate,
            smoothing=smoothing,
            deadzone=deadzone,
            touch_source_width=1920,
            touch_source_height=942,
        ):
            self.reader.stop()
            messagebox.showerror("Sys-Botbase", "Failed to connect to sys-botbase server.")
            return

        self.ip = ip
        self.slot = slot
        self.rate = rate
        self.smoothing = smoothing
        self.deadzone = deadzone

        self.status_var.set(
            f"Connected to {ip} (slot {slot}{', max ' + str(rate) + ' Hz' if rate else ''})"
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
    app = HIDStandaloneSysBotApp()
    app.run()


if __name__ == "__main__":
    main()
