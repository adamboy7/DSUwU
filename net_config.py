import struct
import time
import socket
import zlib
import random

from typing import Optional, Tuple
from dataclasses import dataclass

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