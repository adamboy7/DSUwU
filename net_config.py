import struct
import time
import socket
import zlib
import random

from masks import *

# Server config
UDP_IP = "0.0.0.0"
UDP_PORT = 26760

server_id = random.randint(0, 0xFFFFFFFF)
# {addr: {'last_seen': float, 'slots': set()}}
active_clients = {}
# Tracks which port info has been announced per client
client_port_info = {}
# Set of all slots the server has advertised
known_slots = {0}

# DSU Message Types
DSU_VERSION_REQUEST  = 0x100000
DSU_VERSION_RESPONSE = 0x100000
DSU_LIST_PORTS       = 0x100001
DSU_PORT_INFO        = 0x100001
DSU_PAD_DATA_REQUEST = 0x100002
DSU_PAD_DATA_RESPONSE= 0x100002

PROTOCOL_VERSION = 1001
MAC_ADDRESS = b'\xAA\xBB\xCC\xDD\xEE\xFF'

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind((UDP_IP, UDP_PORT))
sock.setblocking(False)
print(f"Listening on UDP {UDP_IP}:{UDP_PORT}")

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
    state = 2  # connected
    model = 2  # DS4
    connection_type = 2  # USB
    battery = 5  # full
    active = 1  # active
    payload = struct.pack('<4B6s2B', slot, state, model, connection_type, MAC_ADDRESS, battery, active)
    packet = build_header(DSU_PORT_INFO, payload)
    sock.sendto(packet, addr)
    print(f"Sent port info for slot {slot} to {addr}")

def handle_version_request(addr):
    payload = struct.pack('<I H', DSU_VERSION_RESPONSE, PROTOCOL_VERSION)
    packet = build_header(DSU_VERSION_RESPONSE, payload[4:])
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

def send_input(addr, slot, buttons1=button_mask_1(), buttons2=button_mask_2(), home=False,
               touch_button=False, L_stick=(0,0), R_stick=(0,0), R1=False, L1=False,
               R2=0, L2=0, touchpad_input1=None, touchpad_input2=None):
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

    timestamp_ms = int(time.time() * 1000) & 0xFFFFFFFF
    timestamp_us = int(time.time() * 1000000)
    touch1 = touchpad_input1 or touchpad_input()
    touch2 = touchpad_input2 or touchpad_input()

    payload = struct.pack('<4B6s2BI', slot, 2, 2, 2, MAC_ADDRESS, 5, 1, timestamp_ms)
    payload += struct.pack('<22B2H2B2HQ6f',
        buttons1,  # D-Pad Left, D-Pad Down, D-Pad Right, D-Pad Up, Options, R3, L3, Share
        buttons2,  # Y, B, A, X, R1, L1, R2, L2
        int(home),  # home
        int(touch_button),  # padclick (PS4)
        *L_stick,  # left stick (X, Y)
        *R_stick,  # right stick (X, Y)
        0, 0, 0, 0,  # dpad pressures (Purely compliant, PS3)
        0, 0, 0, 0,  # face button pressures (Purely compliant, PS3)
        int(R1),  # PS3 0-255, or True/False
        int(L1),  # PS3 0-255, or True/False
        R2,  # 0-255
        L2,  # 0-255
        *touch1,  # Active, ID, X, Y
        *touch2,  # Active, ID, X, Y
        timestamp_us,
        0.0, 0.0, 0.0, 0.0, 0.0, 0.0
    )
    packet = build_header(DSU_PAD_DATA_RESPONSE, payload)
    sock.sendto(packet, addr)
    print(f"Sent input to {addr} slot {slot}")