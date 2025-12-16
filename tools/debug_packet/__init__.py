from tkinter import Toplevel, Label, scrolledtext, ttk
import struct

from libraries.masks import BATTERY_STATES, CONNECTION_TYPES

__all__ = [
    "PacketParserWindow",
    "describe_packet",
]


def parse_button_request(data: bytes):
    """Return slot number from a DSU input request packet."""
    from protocols.dsu_constants import DSU_button_request

    if len(data) < 24:
        return None
    msg_type, = struct.unpack_from("<I", data, 16)
    if msg_type != DSU_button_request:
        return None
    return {"slot": data[20]}


def parse_port_info(data: bytes):
    """Decode a DSU port info response packet."""
    from protocols.dsu_constants import DSU_port_info

    # Port info packets contain an 11 byte payload plus a 20 byte header and
    # 4 byte message type for a total of 31 bytes. 32 was used previously which
    # prevented parsing valid packets.
    if len(data) < 31:
        return None
    msg_type, = struct.unpack_from("<I", data, 16)
    if msg_type != DSU_port_info:
        return None
    slot, state, model, connection_type, mac, battery = struct.unpack_from(
        "<4B6sB", data, 20
    )
    return {
        "slot": slot,
        "mac": ":".join(f"{b:02X}" for b in mac),
        "connection_type": connection_type,
        "battery": battery,
        "state": state,
        "model": model,
    }


def packet_name(tag: bytes, msg_type: int) -> str:
    from protocols.dsu_constants import (
        DSU_version_request,
        DSU_list_ports,
        DSU_button_request,
        DSU_motor_request,
        motor_command,
    )

    if msg_type == DSU_version_request:
        return "Version Request" if tag == b"DSUC" else "Version Response"
    if msg_type == DSU_list_ports:
        return "List Ports" if tag == b"DSUC" else "Port Info"
    if msg_type == DSU_button_request:
        return "Input Request" if tag == b"DSUC" else "Input Response"
    if msg_type == DSU_motor_request:
        return "Motor Request" if tag == b"DSUC" else "Motor Response"
    if msg_type == motor_command:
        return "Motor Command"
    return f"0x{msg_type:06X}"


def describe_packet(packet: bytes) -> str:

    if len(packet) < 20:
        return "Incomplete packet"
    tag, ver, length, crc, sid = struct.unpack_from("<4sHHII", packet, 0)
    msg_type, = struct.unpack_from("<I", packet, 16)
    lines = [
        f"Tag: {tag.decode(errors='replace')} Protocol: {ver} Length: {length}",
        f"CRC: 0x{crc:08X} Server ID: 0x{sid:08X}",
        "Direction: Client→Server" if tag == b"DSUC" else "Direction: Server→Client",
        f"Message: {packet_name(tag, msg_type)}",
    ]
    name = packet_name(tag, msg_type)
    if name == "Input Response":
        from viewer import parse_button_response  # avoid circular import at top

        state = parse_button_response(packet)
        if state:
            lines.append("")
            lines.append(format_state(state))
    elif name == "Input Request":
        info = parse_button_request(packet)
        if info:
            lines.append(f"Slot: {info['slot']}")
    elif name == "Port Info":
        info = parse_port_info(packet)
        if info:
            lines.append(f"Slot: {info['slot']} MAC: {info['mac']}")
            battery = BATTERY_STATES.get(info["battery"], f"0x{info['battery']:02X}")
            conn = CONNECTION_TYPES.get(info["connection_type"], str(info["connection_type"]))
            lines.append(f"Battery: {battery}")
            lines.append(f"Connection: {conn}")
    return "\n".join(lines)


def format_state(state: dict) -> str:
    if not state:
        return "No data"
    lines = [
        f"Protocol: {state.get('protocol_version', 'unknown')}",
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
        f"Touch1: A={state['touch1']['active']} ID={state['touch1']['id']} Pos={state['touch1']['pos']}",
        f"Touch2: A={state['touch2']['active']} ID={state['touch2']['id']} Pos={state['touch2']['pos']}",
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


class PacketParserWindow:
    def __init__(self, parent):
        self.top = Toplevel(parent)
        self.top.title("Packet Parser")
        self.input = scrolledtext.ScrolledText(self.top, width=80, height=8)
        self.input.pack(fill="both", expand=True)
        btn_frame = ttk.Frame(self.top)
        btn_frame.pack(fill="x")
        self.prev_btn = ttk.Button(btn_frame, text="Prev", command=self.prev_packet, state="disabled")
        self.next_btn = ttk.Button(btn_frame, text="Next", command=self.next_packet, state="disabled")
        self.status = Label(btn_frame, text="0/0")
        self.parse_btn = ttk.Button(btn_frame, text="Parse", command=self.parse_packets)
        self.prev_btn.pack(side="left")
        self.next_btn.pack(side="left")
        self.status.pack(side="left", padx=4)
        self.parse_btn.pack(side="right")
        self.output = scrolledtext.ScrolledText(self.top, width=80, height=15, state="disabled")
        self.output.pack(fill="both", expand=True)
        self.packets: list[bytes] = []
        self.index = -1

    def parse_packets(self):
        raw = self.input.get("1.0", "end")
        hex_str = "".join(ch for ch in raw if ch in "0123456789abcdefABCDEF")
        if len(hex_str) % 2:
            hex_str = hex_str[:-1]
        data = bytes.fromhex(hex_str)
        self.packets.clear()
        offset = 0
        while offset + 16 <= len(data):
            _, _, length, _, _ = struct.unpack_from("<4sHHII", data, offset)
            total = 16 + length
            if offset + total > len(data):
                break
            self.packets.append(data[offset:offset + total])
            offset += total
        self.index = 0 if self.packets else -1
        self.update_view()

    def update_view(self):
        total = len(self.packets)
        if self.index < 0 or total == 0:
            self.prev_btn.config(state="disabled")
            self.next_btn.config(state="disabled")
            self.status.config(text="0/0")
            self.output.config(state="normal")
            self.output.delete("1.0", "end")
            self.output.config(state="disabled")
            return
        self.prev_btn.config(state="normal" if self.index > 0 else "disabled")
        self.next_btn.config(state="normal" if self.index < total - 1 else "disabled")
        self.status.config(text=f"{self.index + 1}/{total}")
        text = describe_packet(self.packets[self.index])
        self.output.config(state="normal")
        self.output.delete("1.0", "end")
        self.output.insert("1.0", text)
        self.output.config(state="disabled")

    def next_packet(self):
        if self.index < len(self.packets) - 1:
            self.index += 1
            self.update_view()

    def prev_packet(self):
        if self.index > 0:
            self.index -= 1
            self.update_view()
