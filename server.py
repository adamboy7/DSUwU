import struct
import time
import socket
import zlib
import random
import select
import threading

from net_config import *
from masks import *
from inputs import controller_loop

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind((UDP_IP, UDP_port))
sock.setblocking(False)
print(f"Listening on UDP {UDP_IP}:{UDP_port}")

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
    payload = struct.pack('<4B6s2B', slot, 2, 2, 2, mac_address, 5, 1)
    packet = build_header(DSU_port_info, payload)
    sock.sendto(packet, addr)
    print(f"Sent port info for slot {slot} to {addr}")

def handle_version_request(addr):
    payload = struct.pack('<I H', DSU_version_response, PROTOCOL_VERSION)
    packet = build_header(DSU_version_response, payload[4:])
    sock.sendto(packet, addr)
    print(f"Sent version response to {addr}")

def handle_list_ports(addr, data):
    if len(data) < 24:
        return
    client_port_info.setdefault(addr, set())
    for slot in sorted(known_slots):
        if slot not in client_port_info[addr]:
            send_port_info(addr, slot)
            client_port_info[addr].add(slot)

def handle_pad_data_request(addr, data):
    if len(data) < 28:
        return
    slot = data[20]
    info = active_clients.setdefault(addr, {'last_seen': time.time(), 'slots': set()})
    info['last_seen'] = time.time()
    info['slots'].add(slot)
    known_slots.add(slot)
    client_port_info.setdefault(addr, set())
    if slot not in client_port_info[addr]:
        send_port_info(addr, slot)
        client_port_info[addr].add(slot)
    print(f"Registered input request from {addr} for slot {slot}")

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
        for client in list(client_port_info.keys()):
            if slot not in client_port_info[client]:
                send_port_info(client, slot)
                client_port_info[client].add(slot)

    client_port_info.setdefault(addr, set())
    if slot not in client_port_info[addr]:
        send_port_info(addr, slot)
        client_port_info[addr].add(slot)

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
    print(f"Sent input to {addr} slot {slot}")

if __name__ == "__main__":
    controller_states = {slot: ControllerState() for slot in range(4)}

    stop_event = threading.Event()

    controller_thread = threading.Thread(
        target=controller_loop,
        args=(stop_event, controller_states),
        daemon=True,
    )
    controller_thread.start()

    next_frame = time.time()

    try:
        while True:
            wait = max(0, next_frame - time.time())
            rlist, _, _ = select.select([sock], [], [], wait)
            if rlist:
                while True:
                    try:
                        data, addr = sock.recvfrom(2048)
                    except BlockingIOError:
                        break
                    if data[:4] == b'DSUC':
                        msg_type, = struct.unpack('<I', data[16:20])
                        if msg_type == DSU_version_request:
                            handle_version_request(addr)
                        elif msg_type == DSU_list_ports:
                            handle_list_ports(addr, data)
                        elif msg_type == DSU_button_request:
                            handle_pad_data_request(addr, data)

            now = time.time()
            for addr in list(active_clients.keys()):
                if now - active_clients[addr]['last_seen'] > DSU_timeout:
                    del active_clients[addr]
                    client_port_info.pop(addr, None)
                    print(f"Client {addr} timed out")

            for addr in active_clients:
                for s, state in controller_states.items():
                    send_input(addr, s, **vars(state))
            for state in controller_states.values():
                state.packet_num = (state.packet_num + 1) & 0xFFFFFFFF

            next_frame += 1 / 60.0

    except KeyboardInterrupt:
        print("Server shutting down.")
    finally:
        stop_event.set()
        controller_thread.join()
        sock.close()