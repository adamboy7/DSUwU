import struct
import time
import socket
import zlib
import threading
import argparse

from net_config import *
from masks import *
from inputs import controller_loop


def parse_server_id(value):
    """Parse a hex server ID ensuring it fits in 32 bits."""
    if value.lower().startswith("0x"):
        value = value[2:]
    if not value:
        raise argparse.ArgumentTypeError("server ID cannot be empty")
    if not all(c in "0123456789abcdefABCDEF" for c in value):
        raise argparse.ArgumentTypeError("server ID must be hexadecimal")
    if len(value) > 8:
        raise argparse.ArgumentTypeError("server ID must be at most 8 hex digits")
    return int(value, 16)


def parse_arguments():
    """Return parsed CLI arguments."""
    parser = argparse.ArgumentParser(description="DSU server")
    parser.add_argument("--port", type=int, help="UDP port to listen on")
    parser.add_argument("--server-id", dest="server_id",
                        type=parse_server_id,
                        help="Server identifier (hex)")
    return parser.parse_args()


sock = None

def crc_packet(header, payload):
    crc_data = header[:8] + b'\x00\x00\x00\x00' + header[12:] + payload
    return zlib.crc32(crc_data) & 0xFFFFFFFF

def build_header(msg_type, payload):
    msg = struct.pack('<I', msg_type) + payload
    length = len(msg)
    header = struct.pack('<4sHHII', b'DSUS', PROTOCOL_VERSION, length, 0, server_id)
    crc = crc_packet(header, msg)
    header = struct.pack('<4sHHII', b'DSUS', PROTOCOL_VERSION, length, crc, server_id)
    return header + msg

def send_port_info(addr, slot):
    mac_address = slot_mac_addresses[slot]
    if not controller_states[slot].connected:
        payload = b"\x00" * 12
    else:
        payload = struct.pack('<4B6s2B', slot, 2, 2, 2, mac_address, 5, 1)
    packet = build_header(DSU_port_info, payload)
    sock.sendto(packet, addr)
    print(f"Sent port info for slot {slot} to {addr}")

def send_port_disconnect(addr, slot):
    """Send a port info packet indicating the slot is disconnected."""
    payload = b"\x00" * 12
    packet = build_header(DSU_port_info, payload)
    sock.sendto(packet, addr)
    print(f"Sent port disconnect for slot {slot} to {addr}")

def handle_version_request(addr):
    payload = struct.pack('<I H', DSU_version_response, PROTOCOL_VERSION)
    packet = build_header(DSU_version_response, payload[4:])
    sock.sendto(packet, addr)
    print(f"Sent version response to {addr}")

def handle_list_ports(addr, data):
    """Respond to a list ports request."""
    if len(data) < 24:
        return
    # Number of ports requested
    count, = struct.unpack_from('<I', data, 20)
    slots = data[24:24 + count]
    for slot in slots:
        if slot in known_slots:
            send_port_info(addr, slot)
        else:
            send_port_disconnect(addr, slot)

def handle_pad_data_request(addr, data):
    if len(data) < 28:
        return
    slot = data[20]
    info = active_clients.setdefault(addr, {'last_seen': time.time(), 'slots': set()})
    info['last_seen'] = time.time()
    info['slots'].add(slot)
    known_slots.add(slot)
    if slot not in logged_pad_requests:
        print(f"Registered input request from {addr} for slot {slot}")
        logged_pad_requests.add(slot)

def handle_motor_request(addr, data):
    """Respond with the number of rumble motors for a controller slot."""
    if len(data) < 28:
        return
    slot = data[20]
    mac_address = slot_mac_addresses[slot]
    motor_count = len(controller_states[slot].motors)
    payload = struct.pack('<4B6s2B', slot, 2, 2, 2, mac_address, 5, 1)
    payload += struct.pack('<B', motor_count)
    packet = build_header(DSU_motor_response, payload)
    sock.sendto(packet, addr)
    print(f"Sent motor count {motor_count} to {addr} slot {slot}")

def handle_motor_command(addr, data):
    """Update rumble motor intensity for a controller slot."""
    if len(data) < 30:
        return
    slot = data[20]
    motor_id = data[28]
    intensity = data[29]
    state = controller_states.get(slot)
    if state is None or motor_id >= len(state.motors):
        return
    motors = list(state.motors)
    timestamps = list(state.motor_timestamps)
    motors[motor_id] = intensity
    timestamps[motor_id] = time.time()
    state.motors = tuple(motors)
    state.motor_timestamps = tuple(timestamps)
    print(f"Rumble motor {motor_id} of slot {slot} set to {intensity}")

def send_input(addr, slot, connected=True, packet_num=0,
               buttons1=button_mask_1(), buttons2=button_mask_2(), home=False,
               touch_button=False, L_stick=(0, 0), R_stick=(0, 0),
               dpad_analog=(0, 0, 0, 0), face_analog=(0, 0, 0, 0),
               analog_R1=0, analog_L1=0, analog_R2=0, analog_L2=0,
               touchpad_input1=None, touchpad_input2=None,
               motion_timestamp=0,
               accelerometer=(0.0, 0.0, 0.0), gyroscope=(0.0, 0.0, 0.0)):
    if slot not in known_slots:
        known_slots.add(slot)
        for client in active_clients.keys():
            send_port_info(client, slot)

    info = active_clients.setdefault(addr, {'last_seen': time.time(), 'slots': set()})
    info['last_seen'] = time.time()
    info['slots'].add(slot)

    counter = packet_num

    motion_ts = motion_timestamp or int(time.time() * 1000000)
    touch1 = touchpad_input1 or touchpad_input()
    touch2 = touchpad_input2 or touchpad_input()

    mac_address = slot_mac_addresses[slot]
    payload = struct.pack('<4B6s2B', slot, 2, 2, 2, mac_address, 5, int(connected))
    payload += struct.pack('<I', counter)
    payload += struct.pack(
        '<BBBBBBBBBBBBBBBBBBBB',
        buttons1,
        buttons2,
        int(home),
        int(touch_button),
        *L_stick,
        *R_stick,
        *dpad_analog,
        *face_analog,
        analog_R1,
        analog_L1,
        analog_R2,
        analog_L2,
    )
    payload += struct.pack('<2B2H', *touch1)
    payload += struct.pack('<2B2H', *touch2)
    payload += struct.pack('<Q', motion_ts)
    payload += struct.pack('<6f', *accelerometer, *gyroscope)
    packet = build_header(DSU_button_response, payload)
    sock.sendto(packet, addr)

    prev_state = last_button_states.get(slot)
    current_state = (buttons1, buttons2)
    if prev_state != current_state:
        print(
            f"Sent input to {addr} slot {slot}: "
            f"buttons1=0x{buttons1:02X} buttons2=0x{buttons2:02X}"
        )
        last_button_states[slot] = current_state

if __name__ == "__main__":
    args = parse_arguments()
    if args.port is not None:
        UDP_port = args.port
    if args.server_id is not None:
        server_id = args.server_id
        print(f"Using server ID 0x{server_id:08X}")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((UDP_IP, UDP_port))
    sock.setblocking(False)
    print(f"Listening on UDP {UDP_IP}:{UDP_port}")

    controller_states = {slot: ControllerState() for slot in range(4)}

    stop_event = threading.Event()

    controller_threads = []
    for slot in controller_states:
        thread = threading.Thread(
            target=controller_loop,
            args=(stop_event, controller_states, slot),
            daemon=True,
        )
        thread.start()
        controller_threads.append(thread)

    try:
        while True:
            try:
                while True:
                    data, addr = sock.recvfrom(2048)
                    if data[:4] == b'DSUC':
                        msg_type, = struct.unpack('<I', data[16:20])
                        if msg_type == DSU_version_request:
                            handle_version_request(addr)
                        elif msg_type == DSU_list_ports:
                            handle_list_ports(addr, data)
                        elif msg_type == DSU_button_request:
                            handle_pad_data_request(addr, data)
                        elif msg_type == DSU_motor_request:
                            handle_motor_request(addr, data)
                        elif msg_type == motor_command:
                            handle_motor_command(addr, data)
            except BlockingIOError:
                pass

            now = time.time()
            for addr in list(active_clients.keys()):
                if now - active_clients[addr]['last_seen'] > DSU_timeout:
                    del active_clients[addr]
                    print(f"Client {addr} timed out")

            for addr in active_clients:
                for s, state in controller_states.items():
                    send_input(
                        addr,
                        s,
                        connected=state.connected,
                        packet_num=state.packet_num,
                        buttons1=state.buttons1,
                        buttons2=state.buttons2,
                        home=state.home,
                        touch_button=state.touch_button,
                        L_stick=state.L_stick,
                        R_stick=state.R_stick,
                        dpad_analog=state.dpad_analog,
                        face_analog=state.face_analog,
                        analog_R1=state.analog_R1,
                        analog_L1=state.analog_L1,
                        analog_R2=state.analog_R2,
                        analog_L2=state.analog_L2,
                        touchpad_input1=state.touchpad_input1,
                        touchpad_input2=state.touchpad_input2,
                        motion_timestamp=state.motion_timestamp,
                        accelerometer=state.accelerometer,
                        gyroscope=state.gyroscope,
                        )
            for state in controller_states.values():
                state.packet_num = (state.packet_num + 1) & 0xFFFFFFFF
                motors = list(state.motors)
                timestamps = list(state.motor_timestamps)
                for i in range(len(motors)):
                    if now - timestamps[i] > DSU_timeout and motors[i] != 0:
                        motors[i] = 0
                state.motors = tuple(motors)

    except KeyboardInterrupt:
        print("Server shutting down.")
    finally:
        stop_event.set()
        for thread in controller_threads:
            thread.join()
        sock.close()
