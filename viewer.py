import struct
import socket
import time
import threading
import logging
from tkinter import Tk, Label, StringVar, BooleanVar
from tkinter import ttk
from tkinter import Menu, simpledialog, filedialog, messagebox

from tools.rebroadcast import Rebroadcaster
from tools.debug_packet import PacketParserWindow, format_state, parse_port_info
from tools.input_capture import InputCapture
from tools.motion_capture import MotionCapture
from tools.sys_botbase import SysBotbaseBridge

from libraries.net_config import UDP_port
import libraries.net_config as net_cfg
from protocols.dsu_constants import (
    PROTOCOL_VERSION,
    DSU_version_request,
    DSU_version_response,
    DSU_list_ports,
    DSU_button_request,
    DSU_button_response,
    DSU_port_info,
)
from protocols.dsu_packet import crc_packet

def build_client_packet(msg_type: int, payload: bytes, protocol_version: int | None = None) -> bytes:
    version = PROTOCOL_VERSION if protocol_version is None else protocol_version
    msg = struct.pack("<I", msg_type) + payload
    length = len(msg)
    header = struct.pack("<4sHHII", b"DSUC", version, length, 0, 0)
    crc = crc_packet(header, msg)
    header = struct.pack("<4sHHII", b"DSUC", version, length, crc, 0)
    return header + msg


def decode_buttons(buttons1: int, buttons2: int) -> dict:
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
        "Y": bool(buttons2 & 0x10),
        "B": bool(buttons2 & 0x20),
        "A": bool(buttons2 & 0x40),
        "X": bool(buttons2 & 0x80),
        "R1": bool(buttons2 & 0x08),
        "L1": bool(buttons2 & 0x04),
        "R2": bool(buttons2 & 0x02),
        "L2": bool(buttons2 & 0x01),
    }


def decode_touch(raw: tuple) -> dict:
    """Return mapping with active flag, id and position from touch tuple."""
    active, tid, x, y = raw
    return {"active": bool(active), "id": tid, "pos": (x, y)}


def parse_button_response(data: bytes):
    if len(data) < 20:
        return None
    protocol_version, = struct.unpack_from("<H", data, 4)
    msg_type, = struct.unpack_from("<I", data, 16)
    if msg_type != DSU_button_response:
        return None

    payload = data[20:]
    fmt_hdr = "<4B6s2B"
    hdr_size = struct.calcsize(fmt_hdr)
    if len(payload) < hdr_size + 4:
        return None

    slot, state, model, connection_type, mac, battery, connected = struct.unpack_from(
        fmt_hdr, payload, 0
    )
    packet_num, = struct.unpack_from("<I", payload, hdr_size)
    offset = hdr_size + 4

    fmt_btns = "<BBBBBBBBBBBBBBBBBBBB"
    btn_size = struct.calcsize(fmt_btns)
    if len(payload) < offset + btn_size:
        return None
    (
        buttons1,
        buttons2,
        home,
        touch_button,
        ls_x,
        ls_y_inverted,
        rs_x,
        rs_y_inverted,
        dpad_left,
        dpad_down,
        dpad_right,
        dpad_up,
        tri,
        cir,
        cro,
        sqr,
        analog_r1,
        analog_l1,
        analog_r2,
        analog_l2,
    ) = struct.unpack_from(fmt_btns, payload, offset)
    offset += btn_size

    touch_fmt = "<2B2H"
    tsize = struct.calcsize(touch_fmt)
    if len(payload) < offset + tsize:
        return None
    touch1_raw = struct.unpack_from(touch_fmt, payload, offset)
    offset += tsize
    if len(payload) < offset + tsize:
        return None
    touch2_raw = struct.unpack_from(touch_fmt, payload, offset)
    offset += tsize

    touch1 = decode_touch(touch1_raw)
    touch2 = decode_touch(touch2_raw)

    if len(payload) < offset + 8:
        return None
    motion_ts, = struct.unpack_from("<Q", payload, offset)
    offset += 8
    accel_gyro_fmt = "<6f"
    if len(payload) < offset + struct.calcsize(accel_gyro_fmt):
        return None
    accel_gyro = struct.unpack_from("<6f", payload, offset)
    accel_x, accel_y, accel_z, gyro_x, gyro_y, gyro_z = accel_gyro

    return {
        "slot": slot,
        "mac": ":".join(f"{b:02X}" for b in mac),
        "packet": packet_num,
        "protocol_version": protocol_version,
        "connected": bool(connected),
        "connection_type": connection_type,
        "battery": battery,
        "buttons1": buttons1,
        "buttons2": buttons2,
        "buttons": decode_buttons(buttons1, buttons2),
        "home": bool(home),
        "touch_button": bool(touch_button),
        "ls": (ls_x, 255 - ls_y_inverted),
        "rs": (rs_x, 255 - rs_y_inverted),
        "dpad": (dpad_up, dpad_right, dpad_down, dpad_left),
        "face": (tri, cir, cro, sqr),
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



class DSUClient:
    def __init__(self, server_ip: str, port: int = UDP_port):
        self.server_ip = server_ip
        self.port = port
        self.addr = (server_ip, port)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("0.0.0.0", 0))
        self.sock.settimeout(0.1)
        self.running = False
        self.thread = None
        self.states = {}
        self.last_request = 0.0
        self.server_states = None
        self.state_callback = None
        self.protocol_version = PROTOCOL_VERSION
        # Start requesting slots beginning at 1. Slot 0 will be discovered
        # automatically if the server reports it.
        self.request_slots = set(range(1, 5))

    @property
    def available_slots(self) -> list[int]:
        """Return a sorted list of discovered controller slots."""
        return sorted(self.request_slots)

    def restart(self, port: int | None = None, server_ip: str | None = None):
        """Restart client communications with an optional new port or server IP."""
        self.stop()
        if port is not None:
            self.port = port
        if server_ip is not None:
            self.server_ip = server_ip
        self.addr = (self.server_ip, self.port)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("0.0.0.0", 0))
        self.sock.settimeout(0.1)
        # Reset initial requested slots to 1-4.
        self.request_slots = set(range(1, 5))
        self.start()

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.thread is not None:
            self.thread.join()
            self.thread = None
        if self.sock is not None:
            self.sock.close()
            self.sock = None

    def _send(self, msg_type: int, payload: bytes = b""):
        try:
            self.sock.sendto(
                build_client_packet(msg_type, payload, self.protocol_version),
                self.addr,
            )
        except OSError as exc:
            logging.error("Failed to send DSU packet: %s", exc)

    def _copy_to_server(self, slot: int, state: dict):
        cs = self.server_states.get(slot)
        if cs is None:
            return
        cs.connected = state["connected"]
        cs.packet_num = state["packet"]
        cs.buttons1 = state["buttons1"]
        cs.buttons2 = state["buttons2"]
        cs.home = state["home"]
        cs.touch_button = state["touch_button"]
        cs.L_stick = tuple(state["ls"])
        cs.R_stick = tuple(state["rs"])
        cs.dpad_analog = tuple(state["dpad"])
        cs.face_analog = tuple(state["face"])
        cs.analog_R1 = state["analog_r1"]
        cs.analog_L1 = state["analog_l1"]
        cs.analog_R2 = state["analog_r2"]
        cs.analog_L2 = state["analog_l2"]
        cs.touchpad_input1 = (
            int(state["touch1"]["active"]),
            state["touch1"]["id"],
            *state["touch1"]["pos"],
        )
        cs.touchpad_input2 = (
            int(state["touch2"]["active"]),
            state["touch2"]["id"],
            *state["touch2"]["pos"],
        )
        cs.motion_timestamp = state["motion_ts"]
        cs.accelerometer = tuple(state["accel"])
        cs.gyroscope = tuple(state["gyro"])
        cs.connection_type = state["connection_type"]
        cs.battery = state["battery"]
        net_cfg.slot_mac_addresses[slot] = bytes.fromhex(state["mac"].replace(":", ""))

    def _loop(self):
        # Handshake
        self._send(DSU_version_request)
        try:
            data, _ = self.sock.recvfrom(2048)
            if struct.unpack_from("<I", data, 16)[0] == DSU_version_response:
                pass
        except socket.timeout:
            pass

        # Request port info for a range of slots to discover controllers
        payload = struct.pack("<I", 16) + bytes(range(16))
        self._send(DSU_list_ports, payload)

        while self.running:
            now = time.time()
            if now - self.last_request > 1.0:
                all_payload = struct.pack("<BB6s", 0, 0, b"\x00" * 6)
                self._send(DSU_button_request, all_payload)
                for slot in sorted(self.request_slots):
                    reg_flags = 0x01
                    mac_bytes = b"\x00" * 6
                    state = self.states.get(slot)
                    if state is not None:
                        try:
                            mac_bytes = bytes.fromhex(state["mac"].replace(":", ""))
                            reg_flags |= 0x02
                        except ValueError:
                            mac_bytes = b"\x00" * 6
                    payload = struct.pack("<BB6s", reg_flags, slot, mac_bytes)
                    self._send(DSU_button_request, payload)
                self.last_request = now

            try:
                data, _ = self.sock.recvfrom(2048)
                try:
                    header_version, = struct.unpack_from("<H", data, 4)
                    self.protocol_version = min(header_version, PROTOCOL_VERSION)
                except struct.error:
                    pass
                msg_type, = struct.unpack_from("<I", data, 16)
                if msg_type == DSU_button_response:
                    try:
                        state = parse_button_response(data)
                    except struct.error as exc:
                        logging.warning("Malformed DSU button response dropped: %s", exc)
                        continue
                    if state:
                        slot = state["slot"]
                        self.states[slot] = state
                        self.request_slots.add(slot)
                        if self.server_states is not None:
                            self._copy_to_server(slot, state)
                        if self.state_callback is not None:
                            try:
                                self.state_callback(slot, state)
                            except Exception as exc:
                                logging.error("State callback failed: %s", exc)
                elif msg_type == DSU_port_info:
                    info = parse_port_info(data)
                    if info:
                        slot = info["slot"]
                        if info.get("state", 0) == 0:
                            # Remove slots reported as disconnected to avoid
                            # phantom controllers like an unused slot 0.
                            self.request_slots.discard(slot)
                            self.states.pop(slot, None)
                        else:
                            self.request_slots.add(slot)
            except socket.timeout:
                pass







class SysBotDialog(simpledialog.Dialog):
    """Combined dialog for configuring Sys-Botbase forwarding."""

    def __init__(self, parent, initial_ip: str | None, slots: list[int] | tuple[int, ...],
                 initial_rate: float | None, initial_smoothing: bool,
                 initial_deadzone: int | None):
        self.initial_ip = initial_ip or ""
        self.initial_rate = initial_rate
        self.initial_smoothing = initial_smoothing
        self.initial_deadzone = initial_deadzone
        self.slots = slots
        self.result: tuple[str, int, float | None, bool, int | None] | None = None
        super().__init__(parent, "Sys-Botbase")

    def body(self, master):
        ttk.Label(master, text="Sys-Botbase IP:").grid(row=0, column=0, sticky="w", padx=4, pady=4)
        self.ip_entry = ttk.Entry(master)
        self.ip_entry.insert(0, self.initial_ip)
        self.ip_entry.grid(row=0, column=1, sticky="ew", padx=4, pady=4)

        ttk.Label(master, text="Controller slot:").grid(row=1, column=0, sticky="w", padx=4, pady=4)
        self.slot_var = StringVar()
        self.slot_combo = ttk.Combobox(
            master,
            textvariable=self.slot_var,
            values=[str(s) for s in self.slots],
        )
        if self.slots:
            self.slot_var.set(str(self.slots[0]))
        self.slot_combo.grid(row=1, column=1, sticky="ew", padx=4, pady=4)

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
        slot_raw = self.slot_var.get().strip()
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


class ViewerUI:
    def __init__(self, client: DSUClient):
        self.client = client
        self.root = Tk()
        self.root.title("DSOwO - Viewer")
        self.capture_menu_index = None
        self.motion_menu_index = None
        self.sysbot_menu_index = None
        self._build_menu()
        self.mode = "tabs"
        self.notebook = ttk.Notebook(self.root)
        self.labels = {}
        self.dropdown = None
        self.dropdown_var = None
        self.single_label = None
        self.rebroadcaster = Rebroadcaster()
        self.capture = InputCapture(self.client)
        self.motion_capture = MotionCapture(self.client)
        self.sys_botbase = SysBotbaseBridge(self.client)
        self._sysbot_ip = self.client.server_ip
        self._sysbot_rate_hz: float | None = None
        self._sysbot_smoothing = False
        self._sysbot_deadzone: int | None = None
        self._sysbot_menu_state = self.sys_botbase.active
        self.parser_win = None
        # Initialize tabs based on discovered slots. If slot 0 is present and
        # there are fewer than 5 slots total, include it; otherwise start at 1.
        slots = self.client.available_slots
        if 0 in slots and len(slots) < 5:
            initial_slots = slots
        else:
            initial_slots = [s for s in slots if s != 0]
        for slot in initial_slots:
            self._ensure_tab(slot)
        self.notebook.pack(fill="both", expand=True)
        self.update()

    def _build_menu(self):
        menu = Menu(self.root)
        self.root.config(menu=menu)
        self.options_menu = Menu(menu, tearoff=0)
        self.tools_menu = Menu(menu, tearoff=0)
        menu.add_cascade(label="Options", menu=self.options_menu)
        menu.add_cascade(label="Tools", menu=self.tools_menu)
        self.options_menu.add_command(label="Port", command=self._change_port)
        self.options_menu.add_command(label="Remote Connection", command=self._change_remote)
        self.tools_menu.add_command(label="Rebroadcast", command=self._start_rebroadcast)
        self.tools_menu.add_command(label="Packet Parser", command=self._open_parser)
        self.tools_menu.add_command(label="Sys-Botbase", command=self._start_sysbot)
        self.sysbot_menu_index = self.tools_menu.index("end")
        self.tools_menu.add_command(label="Start input capture", command=self._start_capture)
        self.capture_menu_index = self.tools_menu.index("end")
        self.tools_menu.add_command(label="Start motion capture", command=self._start_motion_capture)
        self.motion_menu_index = self.tools_menu.index("end")

    def _ensure_tab(self, slot: int) -> None:
        if slot in self.labels:
            return
        frame = ttk.Frame(self.notebook)
        self.notebook.add(frame, text=f"Slot {slot}")
        label = Label(frame, text="No data", justify="left", anchor="nw",
                      font=("Courier", 10))
        label.pack(fill="both", expand=True)
        self.labels[slot] = label

    def _switch_to_dropdown(self) -> None:
        if self.mode == "dropdown":
            return
        self.mode = "dropdown"
        self.notebook.pack_forget()
        self.dropdown_var = StringVar()
        self.dropdown = ttk.Combobox(self.root, textvariable=self.dropdown_var,
                                     state="readonly")
        slots = self.client.available_slots
        self.dropdown["values"] = slots
        if slots:
            self.dropdown_var.set(str(slots[0]))
        self.dropdown.pack(fill="x")
        self.single_label = Label(self.root, text="No data", justify="left",
                                  anchor="nw", font=("Courier", 10))
        self.single_label.pack(fill="both", expand=True)

    def _start_rebroadcast(self):
        port = simpledialog.askinteger(
            "Rebroadcast",
            "Enter rebroadcast port:",
            initialvalue=26761,
            parent=self.root,
        )
        if port is None:
            return
        states = self.rebroadcaster.start(port)
        self.client.server_states = states

    def _open_parser(self):
        if getattr(self, "parser_win", None) is None or not self.parser_win.top.winfo_exists():
            self.parser_win = PacketParserWindow(self.root)
        else:
            self.parser_win.top.lift()

    def _start_capture(self):
        if self.capture.active:
            return
        path = filedialog.asksaveasfilename(
            title="Save input capture",
            defaultextension=".jsonl",
            filetypes=[("JSON Lines", "*.jsonl"), ("All Files", "*.*")],
            parent=self.root,
        )
        if not path:
            return
        if not self.capture.start_capture(path):
            return
        self.tools_menu.entryconfigure(self.capture_menu_index,
                                       label="Stop input capture",
                                       command=self._stop_capture)

    def _stop_capture(self):
        if not self.capture.active:
            return
        self.capture.stop_capture()
        self.tools_menu.entryconfigure(self.capture_menu_index,
                                       label="Start input capture",
                                       command=self._start_capture)

    def _start_motion_capture(self):
        if self.motion_capture.active:
            return
        path = filedialog.asksaveasfilename(
            title="Save motion capture",
            defaultextension=".jsonl",
            filetypes=[("JSON Lines", "*.jsonl"), ("All Files", "*.*")],
            parent=self.root,
        )
        if not path:
            return
        if not self.motion_capture.start_capture(path):
            return
        self.tools_menu.entryconfigure(self.motion_menu_index,
                                       label="Stop motion capture",
                                       command=self._stop_motion_capture)

    def _stop_motion_capture(self):
        if not self.motion_capture.active:
            return
        self.motion_capture.stop_capture()
        self.tools_menu.entryconfigure(self.motion_menu_index,
                                       label="Start motion capture",
                                       command=self._start_motion_capture)

    def _start_sysbot(self):
        dialog = SysBotDialog(
            self.root,
            self._sysbot_ip,
            self.client.available_slots,
            self._sysbot_rate_hz,
            self._sysbot_smoothing,
            self._sysbot_deadzone,
        )
        if dialog.result is None:
            return
        ip, slot, rate, smoothing, deadzone = dialog.result
        self._sysbot_ip = ip
        self._sysbot_rate_hz = rate
        self._sysbot_smoothing = smoothing
        self._sysbot_deadzone = deadzone
        if not self.sys_botbase.start(
            ip,
            slot,
            max_rate_hz=rate,
            smoothing=smoothing,
            deadzone=deadzone,
        ):
            messagebox.showerror("Sys-Botbase", "Failed to connect to sys-botbase server.")
            return
        self.tools_menu.entryconfigure(self.sysbot_menu_index,
                                       label="Stop Sys-Botbase",
                                       command=self._stop_sysbot)
        self._sysbot_menu_state = True

    def _stop_sysbot(self):
        self.sys_botbase.stop()
        self.tools_menu.entryconfigure(self.sysbot_menu_index,
                                       label="Sys-Botbase",
                                       command=self._start_sysbot)
        self._sysbot_menu_state = False

    def _change_port(self):
        port = simpledialog.askinteger(
            "Port",
            "Enter DSU server port:",
            initialvalue=self.client.port,
            parent=self.root,
        )
        if port is not None:
            self.client.restart(port)

    def _change_remote(self):
        ip = simpledialog.askstring(
            "Remote Connection",
            "Enter DSU server IP:",
            initialvalue=self.client.server_ip,
            parent=self.root,
        )
        if ip:
            self.client.restart(server_ip=ip)

    def update(self):
        if self.sysbot_menu_index is not None:
            sysbot_active = self.sys_botbase.active
            if sysbot_active != self._sysbot_menu_state:
                if sysbot_active:
                    self.tools_menu.entryconfigure(self.sysbot_menu_index,
                                                   label="Stop Sys-Botbase",
                                                   command=self._stop_sysbot)
                else:
                    self.tools_menu.entryconfigure(self.sysbot_menu_index,
                                                   label="Sys-Botbase",
                                                   command=self._start_sysbot)
                self._sysbot_menu_state = sysbot_active
        slots = self.client.available_slots
        if self.mode == "tabs":
            if len(slots) > 4:
                self._switch_to_dropdown()
            else:
                for slot in slots:
                    self._ensure_tab(slot)
                for slot, label in self.labels.items():
                    state = self.client.states.get(slot)
                    label.config(text=format_state(state))
        else:
            self.dropdown["values"] = slots
            if slots:
                if self.dropdown_var.get() not in [str(s) for s in slots]:
                    self.dropdown_var.set(str(slots[0]))
                slot = int(self.dropdown_var.get())
                state = self.client.states.get(slot)
                self.single_label.config(text=format_state(state))
            else:
                self.single_label.config(text="No data")
        self.root.after(100, self.update)

    def run(self):
        try:
            self.root.mainloop()
        finally:
            self.rebroadcaster.stop()
            self.capture.stop_capture()
            self.motion_capture.stop_capture()
            self.sys_botbase.stop()


def main(server_ip: str = "127.0.0.1"):
    client = DSUClient(server_ip)
    client.start()
    ui = ViewerUI(client)
    try:
        ui.run()
    finally:
        client.stop()


if __name__ == "__main__":
    main()
