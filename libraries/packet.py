import struct
import zlib
import time

from .net_config import *
from .masks import button_mask_1, button_mask_2, touchpad_input, ControllerState

# Socket used for sending packets. The server assigns this when initialized.
sock = None
# Controller state mapping assigned by the server
controller_states = None


def crc_packet(header: bytes, payload: bytes) -> int:
    """Return CRC32 for a packet."""
    data = header[:8] + b"\x00\x00\x00\x00" + header[12:] + payload
    return zlib.crc32(data) & 0xFFFFFFFF


def build_header(msg_type: int, payload: bytes) -> bytes:
    """Build a DSU packet header for ``msg_type`` and ``payload``."""
    msg = struct.pack('<I', msg_type) + payload
    length = len(msg)
    header = struct.pack('<4sHHII', b'DSUS', PROTOCOL_VERSION, length, 0, server_id)
    crc = crc_packet(header, msg)
    header = struct.pack('<4sHHII', b'DSUS', PROTOCOL_VERSION, length, crc, server_id)
    return header + msg


def send_port_info(addr, slot):
    mac_address = slot_mac_addresses[slot]
    state = controller_states[slot]
    if not state.connected:
        payload = b"\x00" * 12
    else:
        payload = struct.pack(
            '<4B6s2B',
            slot,
            2,
            state.connection_type,
            2,
            mac_address,
            state.battery,
            1,
        )
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
    count, = struct.unpack_from('<I', data, 20)
    slots = data[24:24 + count]
    for slot in slots:
        send_port_info(addr, slot)


def handle_pad_data_request(addr, data):
    if len(data) < 28:
        return
    slot = data[20]
    info = active_clients.setdefault(addr, {'last_seen': time.time(), 'slots': set(), 'known_slots': set()})
    info['last_seen'] = time.time()
    state = controller_states.get(slot)
    if state and state.connected:
        info['slots'].add(slot)
        if slot not in info['known_slots']:
            send_port_info(addr, slot)
            info['known_slots'].add(slot)
    else:
        if slot not in info['known_slots']:
            send_port_disconnect(addr, slot)
            info['known_slots'].add(slot)
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


def send_input(
    addr,
    slot,
    connected=True,
    packet_num=0,
    buttons1=button_mask_1(),
    buttons2=button_mask_2(),
    home=False,
    touch_button=False,
    L_stick=(0, 0),
    R_stick=(0, 0),
    dpad_analog=(0, 0, 0, 0),
    face_analog=(0, 0, 0, 0),
    analog_R1=0,
    analog_L1=0,
    analog_R2=0,
    analog_L2=0,
    touchpad_input1=None,
    touchpad_input2=None,
    motion_timestamp=0,
    accelerometer=(0.0, 0.0, 0.0),
    gyroscope=(0.0, 0.0, 0.0),
    connection_type=2,
    battery=5,
):
    for client, info in active_clients.items():
        if slot not in info.get('known_slots', set()):
            send_port_info(client, slot)
            info.setdefault('known_slots', set()).add(slot)

    info = active_clients.setdefault(addr, {'last_seen': time.time(), 'slots': set(), 'known_slots': set()})
    info['last_seen'] = time.time()
    info['slots'].add(slot)

    counter = packet_num

    motion_ts = motion_timestamp or int(time.time() * 1000000)
    touch1 = touchpad_input1 or touchpad_input()
    touch2 = touchpad_input2 or touchpad_input()

    mac_address = slot_mac_addresses[slot]
    payload = struct.pack(
        '<4B6s2B',
        slot,
        2,
        connection_type,
        2,
        mac_address,
        battery,
        int(connected),
    )
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

