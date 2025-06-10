import random

# Server config
UDP_IP = "0.0.0.0"
UDP_port = 26760
DSU_timeout = 5.0

# Server state tracking
server_id = random.randint(0, 0xFFFFFFFF)
# {addr: {'last_seen': float, 'slots': set()}}
active_clients = {}
# Set of all slots the server has advertised
known_slots = {0}

# DSU Message Types
DSU_version_request  = 0x100000
DSU_version_response = 0x100000
DSU_list_ports       = 0x100001
DSU_port_info        = 0x100001
DSU_button_request = 0x100002
DSU_button_response = 0x100002
DSU_motor_request = 0x110001
DSU_motor_response = 0x110001
motor_command = 0x110002

PROTOCOL_VERSION = 1001

# Unique MAC addresses per controller slot
slot1_mac_address = b'\xAA\xBB\xCC\xDD\xEE\x01'
slot2_mac_address = b'\xAA\xBB\xCC\xDD\xEE\x02'
slot3_mac_address = b'\xAA\xBB\xCC\xDD\xEE\x03'
slot4_mac_address = b'\xAA\xBB\xCC\xDD\xEE\x04'

slot_mac_addresses = [
    slot1_mac_address,
    slot2_mac_address,
    slot3_mac_address,
    slot4_mac_address,
]
