import zlib
import struct
import socket
import time
import threading
import logging
from tkinter import Tk, Label
from tkinter import ttk
from tkinter import Menu, simpledialog, messagebox

from libraries.masks import BATTERY_STATES, CONNECTION_TYPES

from libraries.net_config import (
    UDP_port,
    PROTOCOL_VERSION,
    DSU_version_request,
    DSU_version_response,
    DSU_list_ports,
    DSU_button_request,
    DSU_button_response,
    DSU_timeout,
)
import libraries.net_config as net_cfg
from server import start_server


def crc_packet(header: bytes, payload: bytes) -> int:
    data = header[:8] + b"\x00\x00\x00\x00" + header[12:] + payload
    return zlib.crc32(data) & 0xFFFFFFFF

def build_client_packet(msg_type: int, payload: bytes) -> bytes:
    msg = struct.pack("<I", msg_type) + payload
    length = len(msg)
    header = struct.pack("<4sHHII", b"DSUC", PROTOCOL_VERSION, length, 0, 0)
    crc = crc_packet(header, msg)
    header = struct.pack("<4sHHII", b"DSUC", PROTOCOL_VERSION, length, crc, 0)
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
    msg_type, = struct.unpack_from("<I", data, 16)
    if msg_type != DSU_button_response:
        return None

    payload = data[20:]
    fmt_hdr = "<4B6s2B"
    hdr_size = struct.calcsize(fmt_hdr)
    if len(payload) < hdr_size + 4:
        return None

    slot, model, connection_type, _, mac, battery, connected = struct.unpack_from(fmt_hdr, payload, 0)
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
        ls_y,
        rs_x,
        rs_y,
        dpad_up,
        dpad_right,
        dpad_down,
        dpad_left,
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
    touch1_raw = struct.unpack_from(touch_fmt, payload, offset)
    offset += tsize
    touch2_raw = struct.unpack_from(touch_fmt, payload, offset)
    offset += tsize

    touch1 = decode_touch(touch1_raw)
    touch2 = decode_touch(touch2_raw)

    motion_ts, = struct.unpack_from("<Q", payload, offset)
    offset += 8
    accel_gyro = struct.unpack_from("<6f", payload, offset)

    return {
        "slot": slot,
        "mac": ":".join(f"{b:02X}" for b in mac),
        "packet": packet_num,
        "connected": bool(connected),
        "connection_type": connection_type,
        "battery": battery,
        "buttons1": buttons1,
        "buttons2": buttons2,
        "buttons": decode_buttons(buttons1, buttons2),
        "home": bool(home),
        "touch_button": bool(touch_button),
        "ls": (ls_x, ls_y),
        "rs": (rs_x, rs_y),
        "dpad": (dpad_up, dpad_right, dpad_down, dpad_left),
        "face": (tri, cir, cro, sqr),
        "analog_r1": analog_r1,
        "analog_l1": analog_l1,
        "analog_r2": analog_r2,
        "analog_l2": analog_l2,
        "touch1": touch1,
        "touch2": touch2,
        "motion_ts": motion_ts,
        "accel": accel_gyro[:3],
        "gyro": accel_gyro[3:],
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
            self.sock.sendto(build_client_packet(msg_type, payload), self.addr)
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

        # Request port info for 4 slots
        payload = struct.pack("<I", 4) + bytes(range(4))
        self._send(DSU_list_ports, payload)

        while self.running:
            now = time.time()
            if now - self.last_request > 1.0:
                for slot in range(4):
                    pld = struct.pack("B", slot) + b"\x00" * 7
                    self._send(DSU_button_request, pld)
                self.last_request = now

            try:
                data, _ = self.sock.recvfrom(2048)
                state = parse_button_response(data)
                if state:
                    slot = state["slot"]
                    self.states[slot] = state
                    if self.server_states is not None:
                        self._copy_to_server(slot, state)
            except socket.timeout:
                pass


class VirtualController:
    """Bridge DSU input to a virtual controller via vgamepad."""

    def __init__(self, client: DSUClient, slot: int = 0):
        self.client = client
        self.slot = slot
        self.gamepad = None
        self.running = False
        self.thread = None
        self.vg = None

    def start(self) -> bool:
        try:
            import vgamepad as vg
        except Exception:
            messagebox.showerror(
                "Virtual Controller",
                "The 'vgamepad' package is required for this tool."
            )
            return False
        self.vg = vg
        self.gamepad = vg.VDS4Gamepad()
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()
        return True

    def stop(self) -> None:
        self.running = False
        if self.thread is not None:
            self.thread.join()
            self.thread = None
        self.gamepad = None

    def _loop(self) -> None:
        while self.running:
            state = self.client.states.get(self.slot)
            if state:
                self._apply_state(state)
            time.sleep(1 / 60.0)

    def _apply_state(self, state: dict) -> None:
        vg = self.vg
        gp = self.gamepad
        btns = state["buttons"]

        mapping = [
            ("A", vg.DS4_BUTTONS.DS4_BUTTON_CROSS),
            ("B", vg.DS4_BUTTONS.DS4_BUTTON_CIRCLE),
            ("X", vg.DS4_BUTTONS.DS4_BUTTON_SQUARE),
            ("Y", vg.DS4_BUTTONS.DS4_BUTTON_TRIANGLE),
            ("L1", vg.DS4_BUTTONS.DS4_BUTTON_SHOULDER_LEFT),
            ("R1", vg.DS4_BUTTONS.DS4_BUTTON_SHOULDER_RIGHT),
            ("L3", vg.DS4_BUTTONS.DS4_BUTTON_THUMB_LEFT),
            ("R3", vg.DS4_BUTTONS.DS4_BUTTON_THUMB_RIGHT),
            ("Share", vg.DS4_BUTTONS.DS4_BUTTON_SHARE),
            ("Options", vg.DS4_BUTTONS.DS4_BUTTON_OPTIONS),
        ]
        for name, btn in mapping:
            if btns.get(name):
                gp.press_button(button=btn)
            else:
                gp.release_button(button=btn)

        if state["home"]:
            gp.press_button(button=vg.DS4_BUTTONS.DS4_BUTTON_PS)
        else:
            gp.release_button(button=vg.DS4_BUTTONS.DS4_BUTTON_PS)

        if state["touch_button"]:
            gp.press_button(button=vg.DS4_BUTTONS.DS4_BUTTON_TOUCHPAD)
        else:
            gp.release_button(button=vg.DS4_BUTTONS.DS4_BUTTON_TOUCHPAD)

        up = btns["D-Pad Up"]
        down = btns["D-Pad Down"]
        left = btns["D-Pad Left"]
        right = btns["D-Pad Right"]

        direction = vg.DS4_DPAD_DIRECTIONS.DS4_BUTTON_DPAD_NONE
        if up and right:
            direction = vg.DS4_DPAD_DIRECTIONS.DS4_BUTTON_DPAD_NORTHEAST
        elif up and left:
            direction = vg.DS4_DPAD_DIRECTIONS.DS4_BUTTON_DPAD_NORTHWEST
        elif down and right:
            direction = vg.DS4_DPAD_DIRECTIONS.DS4_BUTTON_DPAD_SOUTHEAST
        elif down and left:
            direction = vg.DS4_DPAD_DIRECTIONS.DS4_BUTTON_DPAD_SOUTHWEST
        elif up:
            direction = vg.DS4_DPAD_DIRECTIONS.DS4_BUTTON_DPAD_NORTH
        elif down:
            direction = vg.DS4_DPAD_DIRECTIONS.DS4_BUTTON_DPAD_SOUTH
        elif left:
            direction = vg.DS4_DPAD_DIRECTIONS.DS4_BUTTON_DPAD_WEST
        elif right:
            direction = vg.DS4_DPAD_DIRECTIONS.DS4_BUTTON_DPAD_EAST
        gp.directional_pad(direction=direction)

        lx = (state["ls"][0] - 128) / 127.0
        ly = (state["ls"][1] - 128) / 127.0
        rx = (state["rs"][0] - 128) / 127.0
        ry = (state["rs"][1] - 128) / 127.0
        gp.left_joystick_float(x_value_float=lx, y_value_float=-ly)
        gp.right_joystick_float(x_value_float=rx, y_value_float=-ry)
        gp.left_trigger(value=state["analog_l2"])
        gp.right_trigger(value=state["analog_r2"])
        gp.update()


def format_state(state: dict) -> str:
    if not state:
        return "No data"
    lines = [
        f"MAC: {state['mac']}",
        f"Connected: {state['connected']}",
        f"Packet: {state['packet']}",
        f"Buttons1: 0x{state['buttons1']:02X}",
        f"Buttons2: 0x{state['buttons2']:02X}",
        f"Home: {state['home']} Touch: {state['touch_button']}",
        f"LS: {state['ls']} RS: {state['rs']}",
        f"Dpad: {state['dpad']} Face: {state['face']}",
        f"Analog L1/R1: {state['analog_l1']} {state['analog_r1']}",
        f"Analog L2/R2: {state['analog_l2']} {state['analog_r2']}",
        f"Touch1: A={state['touch1']['active']} ID={state['touch1']['id']}"
        f" Pos={state['touch1']['pos']}",
        f"Touch2: A={state['touch2']['active']} ID={state['touch2']['id']}"
        f" Pos={state['touch2']['pos']}",
        f"Gyro: {state['gyro']}",
        f"Accel: {state['accel']}",
    ]
    lines.append("Buttons:")
    for name, pressed in state["buttons"].items():
        lines.append(f"  {name}: {pressed}")
    battery_text = BATTERY_STATES.get(state["battery"], f"0x{state['battery']:02X}")
    lines.append(f"Battery: {battery_text}")
    conn_text = CONNECTION_TYPES.get(state["connection_type"], str(state["connection_type"]))
    lines.append(f"Connection: {conn_text}")
    return "\n".join(lines)


class ViewerUI:
    def __init__(self, client: DSUClient):
        self.client = client
        self.root = Tk()
        self.root.title("DSOwO - Viewer")
        self._build_menu()
        self.notebook = ttk.Notebook(self.root)
        self.labels = {}
        self.rebroadcast_stop = None
        self.rebroadcast_thread = None
        self.virtual_controller = None
        self.virtual_menu_index = None
        for slot in range(4):
            frame = ttk.Frame(self.notebook)
            self.notebook.add(frame, text=f"Slot {slot}")
            label = Label(frame, text="No data", justify="left", anchor="nw",
                          font=("Courier", 10))
            label.pack(fill="both", expand=True)
            self.labels[slot] = label
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
        self.tools_menu.add_command(label="Virtual Controller", command=self._toggle_virtual)
        self.virtual_menu_index = self.tools_menu.index("end")

    def _start_rebroadcast(self):
        port = simpledialog.askinteger(
            "Rebroadcast",
            "Enter rebroadcast port:",
            initialvalue=26761,
            parent=self.root,
        )
        if port is None:
            return
        if self.rebroadcast_stop is not None:
            self.rebroadcast_stop.set()
            if self.rebroadcast_thread is not None:
                self.rebroadcast_thread.join(timeout=0.1)
        states, stop_evt, thread = start_server(port=port, scripts=[None] * 4)
        self.client.server_states = states
        self.rebroadcast_stop = stop_evt
        self.rebroadcast_thread = thread

    def _toggle_virtual(self):
        if self.virtual_controller is not None:
            self.virtual_controller.stop()
            self.virtual_controller = None
            self.tools_menu.entryconfig(self.virtual_menu_index,
                                        label="Virtual Controller")
            return

        slot = simpledialog.askinteger(
            "Virtual Controller",
            "Enter controller slot:",
            initialvalue=0,
            parent=self.root,
            minvalue=0,
            maxvalue=3,
        )
        if slot is None:
            return
        vc = VirtualController(self.client, slot)
        if vc.start():
            self.virtual_controller = vc
            self.tools_menu.entryconfig(self.virtual_menu_index,
                                        label="Stop Virtual Controller")

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
        for slot in range(4):
            state = self.client.states.get(slot)
            self.labels[slot].config(text=format_state(state))
        self.root.after(100, self.update)

    def run(self):
        try:
            self.root.mainloop()
        finally:
            if self.rebroadcast_stop is not None:
                self.rebroadcast_stop.set()
                self.rebroadcast_thread.join(timeout=0.1)
            if self.virtual_controller is not None:
                self.virtual_controller.stop()


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
