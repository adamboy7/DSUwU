import zlib
import struct
import socket
import time
import threading
from tkinter import Tk, Label
from tkinter import ttk
from tkinter import Menu, simpledialog

# Mapping tables for battery state and connection type values
BATTERY_STATES = {
    0x00: "Not applicable",
    0x01: "Dying",
    0x02: "Low",
    0x03: "Medium",
    0x04: "High",
    0x05: "Full (or almost)",
    0xEE: "Charging",
    0xEF: "Charged",
}

CONNECTION_TYPES = {
    0: "N/A",
    1: "USB",
    2: "Bluetooth",
}

from net_config import (
    UDP_port,
    PROTOCOL_VERSION,
    DSU_version_request,
    DSU_version_response,
    DSU_list_ports,
    DSU_button_request,
    DSU_button_response,
    DSU_port_info,
    DSU_motor_request,
    DSU_motor_response,
)


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
        self.rebroadcast_port = None
        self.rebroadcast_thread = None
        self.rebroadcast_running = False
        self.server_id = 0
        self.running = False
        self.thread = None
        self.states = {}
        self.last_request = 0.0

    def restart(self, port: int):
        """Restart client communications on a new port."""
        self.stop()
        self.sock.close()
        self.port = port
        self.addr = (self.server_ip, port)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("0.0.0.0", 0))
        self.sock.settimeout(0.1)
        self.server_id = 0
        self.start()

    def set_rebroadcast(self, port: int | None):
        """Start or stop rebroadcasting controller states on the given port."""
        if self.rebroadcast_running:
            self.rebroadcast_running = False
            if self.rebroadcast_thread:
                self.rebroadcast_thread.join(timeout=0.1)
                self.rebroadcast_thread = None
        self.rebroadcast_port = port
        if port is not None:
            self.rebroadcast_running = True
            self.rebroadcast_thread = threading.Thread(
                target=self._rebroadcast_loop,
                daemon=True,
            )
            self.rebroadcast_thread.start()

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.thread is not None:
            self.thread.join(timeout=0.1)
        if self.rebroadcast_running:
            self.rebroadcast_running = False
            if self.rebroadcast_thread:
                self.rebroadcast_thread.join(timeout=0.1)
                self.rebroadcast_thread = None

    def _send(self, msg_type: int, payload: bytes = b""):
        self.sock.sendto(build_client_packet(msg_type, payload), self.addr)

    def _loop(self):
        # Handshake - retry until we get the server ID
        attempts = 0
        while self.running and self.server_id == 0:
            self._send(DSU_version_request)
            try:
                data, _ = self.sock.recvfrom(2048)
                if struct.unpack_from("<I", data, 16)[0] == DSU_version_response:
                    self.server_id = struct.unpack_from("<I", data, 12)[0]
                    break
            except socket.timeout:
                pass
            attempts += 1
            if attempts >= 10:
                attempts = 0
                time.sleep(0.5)

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
                    self.states[state["slot"]] = state
            except socket.timeout:
                pass

    def _build_server_packet(self, msg_type: int, payload: bytes) -> bytes:
        msg = struct.pack("<I", msg_type) + payload
        length = len(msg)
        header = struct.pack(
            "<4sHHII",
            b"DSUS",
            PROTOCOL_VERSION,
            length,
            0,
            self.server_id,
        )
        crc = crc_packet(header, msg)
        header = struct.pack(
            "<4sHHII",
            b"DSUS",
            PROTOCOL_VERSION,
            length,
            crc,
            self.server_id,
        )
        return header + msg

    def _rebroadcast_loop(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("0.0.0.0", self.rebroadcast_port))
        sock.settimeout(0.1)

        # Wait until we've learned the server ID from the original server
        while self.rebroadcast_running and self.server_id == 0:
            time.sleep(0.05)

        while self.rebroadcast_running:
            if self.server_id == 0:
                # If the viewer reconnects to a different server, pause until we
                # know the new server ID so clients get a valid handshake.
                time.sleep(0.05)
                continue
            try:
                data, addr = sock.recvfrom(2048)
            except socket.timeout:
                continue
            if data[:4] != b"DSUC":
                continue
            msg_type, = struct.unpack_from("<I", data, 16)
            if msg_type == DSU_version_request:
                payload = struct.pack("<I H", DSU_version_response, PROTOCOL_VERSION)[4:]
                packet = self._build_server_packet(DSU_version_response, payload)
                sock.sendto(packet, addr)
            elif msg_type == DSU_list_ports:
                if len(data) < 24:
                    continue
                count, = struct.unpack_from("<I", data, 20)
                slots = data[24:24 + count]
                for slot in slots:
                    state = self.states.get(slot)
                    if state:
                        mac = bytes.fromhex(state["mac"].replace(":", ""))
                        payload = struct.pack(
                            "<4B6s2B",
                            slot,
                            2,
                            state["connection_type"],
                            2,
                            mac,
                            state["battery"],
                            int(state["connected"]),
                        )
                    else:
                        payload = b"\x00" * 12
                    packet = self._build_server_packet(DSU_port_info, payload)
                    sock.sendto(packet, addr)
            elif msg_type == DSU_button_request:
                if len(data) < 21:
                    continue
                slot = data[20]
                state = self.states.get(slot)
                if not state:
                    continue
                mac = bytes.fromhex(state["mac"].replace(":", ""))
                payload = struct.pack(
                    "<4B6s2B",
                    slot,
                    2,
                    state["connection_type"],
                    2,
                    mac,
                    state["battery"],
                    int(state["connected"]),
                )
                payload += struct.pack("<I", state["packet"])
                payload += struct.pack(
                    "<BBBBBBBBBBBBBBBBBBBB",
                    state["buttons1"],
                    state["buttons2"],
                    int(state["home"]),
                    int(state["touch_button"]),
                    state["ls"][0],
                    state["ls"][1],
                    state["rs"][0],
                    state["rs"][1],
                    state["dpad"][0],
                    state["dpad"][1],
                    state["dpad"][2],
                    state["dpad"][3],
                    state["face"][0],
                    state["face"][1],
                    state["face"][2],
                    state["face"][3],
                    state["analog_r1"],
                    state["analog_l1"],
                    state["analog_r2"],
                    state["analog_l2"],
                )
                payload += struct.pack(
                    "<2B2H",
                    int(state["touch1"]["active"]),
                    state["touch1"]["id"],
                    state["touch1"]["pos"][0],
                    state["touch1"]["pos"][1],
                )
                payload += struct.pack(
                    "<2B2H",
                    int(state["touch2"]["active"]),
                    state["touch2"]["id"],
                    state["touch2"]["pos"][0],
                    state["touch2"]["pos"][1],
                )
                payload += struct.pack("<Q", state["motion_ts"])
                payload += struct.pack("<6f", *state["accel"], *state["gyro"])
                packet = self._build_server_packet(DSU_button_response, payload)
                sock.sendto(packet, addr)
            elif msg_type == DSU_motor_request:
                if len(data) < 28:
                    continue
                slot = data[20]
                state = self.states.get(slot)
                mac = bytes.fromhex(state["mac"].replace(":", "")) if state else b"\x00" * 6
                conn = state["connection_type"] if state else 0
                battery = state["battery"] if state else 0
                connected = int(state["connected"]) if state else 0
                payload = struct.pack(
                    "<4B6s2B",
                    slot,
                    2,
                    conn,
                    2,
                    mac,
                    battery,
                    connected,
                )
                payload += struct.pack("<B", 0)
                packet = self._build_server_packet(DSU_motor_response, payload)
                sock.sendto(packet, addr)
        sock.close()


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
        self.root.title("DSU Input Viewer")
        self._build_menu()
        self.notebook = ttk.Notebook(self.root)
        self.labels = {}
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
        self.tools_menu.add_command(label="Rebroadcast", command=self._set_rebroadcast)

    def _change_port(self):
        port = simpledialog.askinteger(
            "Port",
            "Enter DSU server port:",
            initialvalue=self.client.port,
            parent=self.root,
        )
        if port is not None:
            self.client.restart(port)

    def _set_rebroadcast(self):
        port = simpledialog.askinteger(
            "Rebroadcast",
            "Rebroadcast to port:",
            initialvalue=self.client.rebroadcast_port or 26761,
            parent=self.root,
        )
        if port is not None:
            self.client.set_rebroadcast(port)

    def update(self):
        for slot in range(4):
            state = self.client.states.get(slot)
            self.labels[slot].config(text=format_state(state))
        self.root.after(100, self.update)

    def run(self):
        self.root.mainloop()


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
