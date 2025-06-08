import struct
import time
import socket
import zlib
import random

# Server config
UDP_IP = "0.0.0.0"
UDP_PORT = 26760

server_id = random.randint(0, 0xFFFFFFFF)
active_clients = {}

# DSU Message Types
DSU_VERSION_REQUEST = 0x100000
DSU_VERSION_RESPONSE = 0x100000
DSU_LIST_PORTS = 0x100001
DSU_PORT_INFO = 0x100001
DSU_PAD_DATA_REQUEST = 0x100002
DSU_PAD_DATA_RESPONSE = 0x100002

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

def handle_version_request(addr):
    payload = struct.pack('<I H', DSU_VERSION_RESPONSE, PROTOCOL_VERSION)
    packet = build_header(DSU_VERSION_RESPONSE, payload[4:])
    sock.sendto(packet, addr)
    print(f"Sent version response to {addr}")

def handle_list_ports(addr, data):
    if len(data) < 24:
        return
    # Optionally parse wanted_slots, but always send just slot 0
    slot = 0
    state = 2  # connected
    model = 2  # DS4
    connection_type = 2  # USB
    battery = 5  # full
    active = 1  # active
    payload = struct.pack('<I4B6s2B', DSU_PORT_INFO, slot, state, model, connection_type, MAC_ADDRESS, battery, active)
    packet = build_header(DSU_PORT_INFO, payload[4:])
    sock.sendto(packet, addr)
    print(f"Sent port info for slot {slot} to {addr}")


def handle_pad_data_request(addr, data):
    if len(data) < 28:
        return
    slot = 0  # Force slot 0
    active_clients[addr] = (time.time(), slot)
    print(f"Registered input request from {addr} for slot {slot}")
