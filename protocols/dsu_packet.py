import struct
import zlib
import time
import queue
import threading
import socket

# Import libraries from the project root. These modules are not part of a
# package hierarchy above ``protocols`` so absolute imports are required when
# this file is executed as part of the application.
from libraries import net_config as net_cfg
from libraries.masks import button_mask_1, button_mask_2, touchpad_input
from .dsu_constants import (
    DSU_port_info,
    DSU_version_response,
    DSU_motor_response,
    DSU_button_response,
    PROTOCOL_VERSION,
)


def crc_packet(header: bytes, payload: bytes) -> int:
    """Return CRC32 for a packet."""
    data = header[:8] + b"\x00\x00\x00\x00" + header[12:] + payload
    return zlib.crc32(data) & 0xFFFFFFFF

# Socket used for sending packets. The server assigns this when initialized.
sock = None
# Controller state mapping assigned by the server
controller_states = None

# Queue and thread for asynchronous packet sends
send_queue: queue.Queue[tuple[bytes, tuple[str, int], str | None]] | None = None
send_thread: threading.Thread | None = None
_send_stop: threading.Event | None = None


def start_sender(send_sock: socket.socket, stop_event: threading.Event) -> None:
    """Start a background thread that flushes queued packets."""
    global sock, send_queue, send_thread, _send_stop
    sock = send_sock
    send_queue = queue.Queue()
    _send_stop = stop_event

    def _worker() -> None:
        assert send_queue is not None
        while not _send_stop.is_set():
            try:
                pkt, addr, desc = send_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                send_sock.sendto(pkt, addr)
            except OSError as exc:
                if desc:
                    print(f"Failed to send {desc} to {addr}: {exc}")
                else:
                    print(f"Failed to send packet to {addr}: {exc}")
                if net_cfg.active_clients.pop(addr, None) is not None:
                    print(f"Removed client {addr} after send failure")
            finally:
                send_queue.task_done()

    send_thread = threading.Thread(target=_worker, daemon=True)
    send_thread.start()


def stop_sender() -> None:
    """Join the sender thread if running."""
    if send_thread is not None:
        send_thread.join()


def queue_packet(pkt: bytes, addr: tuple[str, int], desc: str | None = None) -> None:
    """Queue a packet for asynchronous sending."""
    if send_queue is None:
        try:
            sock.sendto(pkt, addr)
        except OSError as exc:
            if desc:
                print(f"Failed to send {desc} to {addr}: {exc}")
            else:
                print(f"Failed to send packet to {addr}: {exc}")
            if net_cfg.active_clients.pop(addr, None) is not None:
                print(f"Removed client {addr} after send failure")
        return

    send_queue.put((pkt, addr, desc))


def build_header(msg_type: int, payload: bytes, protocol_version: int | None = None) -> bytes:
    """Build a DSU packet header for ``msg_type`` and ``payload``."""
    version = PROTOCOL_VERSION if protocol_version is None else protocol_version
    msg = struct.pack('<I', msg_type) + payload
    length = len(msg)
    header = struct.pack('<4sHHII', b'DSUS', version, length, 0, net_cfg.server_id)
    crc = crc_packet(header, msg)
    header = struct.pack('<4sHHII', b'DSUS', version, length, crc, net_cfg.server_id)
    return header + msg


def send_port_info(addr, slot, protocol_version: int | None = None):
    if slot >= net_cfg.soft_slot_limit:
        print("Warning: slots above 255 cannot be reported to the client")
        return
    state = controller_states[slot]
    if state.connection_type == -1:
        payload = b"\x00" * 11
    else:
        mac_address = net_cfg.slot_mac_addresses[slot]
        payload = struct.pack(
            '<4B6sB',
            slot,
            2,  # slot state - connected
            2,  # device model - full gyro
            state.connection_type,
            mac_address,
            state.battery,
        )
    packet = build_header(DSU_port_info, payload, protocol_version=protocol_version)
    queue_packet(packet, addr, f"port info slot {slot}")


def send_port_disconnect(addr, slot, protocol_version: int | None = None):
    """Send a port info packet indicating the slot is disconnected."""
    if slot >= net_cfg.soft_slot_limit:
        print("Warning: slots above 255 cannot be reported to the client")
        return
    # Include the slot number in the payload so clients know which
    # controller was disconnected. Older behaviour filled the entire
    # payload with zeros which always reported slot 0, leading clients
    # to believe an extra controller existed.
    payload = struct.pack("<4B6sB", slot, 0, 0, 0, b"\x00" * 6, 0)
    packet = build_header(DSU_port_info, payload, protocol_version=protocol_version)
    queue_packet(packet, addr, f"port disconnect slot {slot}")


def handle_version_request(addr, protocol_version: int):
    payload = struct.pack('<I H', DSU_version_response, PROTOCOL_VERSION)
    packet = build_header(DSU_version_response, payload[4:], protocol_version=protocol_version)
    info = net_cfg.ensure_client(addr)
    info['last_seen'] = time.time()
    queue_packet(packet, addr, "version response")


def handle_list_ports(addr, data, protocol_version: int | None = None):
    """Respond to a list ports request."""
    if len(data) < 24:
        return
    info = net_cfg.ensure_client(addr)
    info['last_seen'] = time.time()
    count, = struct.unpack_from('<I', data, 20)
    slots = data[24:24 + count]
    for slot in slots:
        if slot in net_cfg.known_slots:
            send_port_info(addr, slot, protocol_version=protocol_version)
        else:
            send_port_disconnect(addr, slot, protocol_version=protocol_version)


def handle_pad_data_request(addr, data):
    if len(data) < 28:
        return
    reg_flags = data[20]
    requested_slot = data[21]
    mac = data[22:28]
    info = net_cfg.ensure_client(addr)
    info['last_seen'] = time.time()
    info['registrations'].setdefault('slots', {})
    info['registrations'].setdefault('macs', {})
    now = time.time()
    if reg_flags == 0:
        info['registrations']['all'] = now
    if reg_flags & 0x01:
        info['registrations']['slots'][requested_slot] = now
        info['slots'].add(requested_slot)
        state = controller_states.get(requested_slot)
        if state is not None and state.connected:
            net_cfg.known_slots.add(requested_slot)
        if requested_slot not in net_cfg.logged_pad_requests:
            print(f"Registered input request from {addr} for slot {requested_slot}")
            net_cfg.logged_pad_requests.add(requested_slot)
    if reg_flags & 0x02 and mac != b"\x00" * 6:
        info['registrations']['macs'][mac] = now


def handle_motor_request(addr, data, protocol_version: int | None = None):
    """Respond with the number of rumble motors for a controller slot."""
    if len(data) < 28:
        return
    slot = data[20]
    if slot >= net_cfg.soft_slot_limit:
        print("Warning: slots above 255 cannot be reported to the client")
        return
    info = net_cfg.ensure_client(addr)
    info['last_seen'] = time.time()
    info['slots'].add(slot)
    state = controller_states.get(slot)
    if (
        state is None
        or state.connection_type == -1
        or not state.connected
        or slot not in net_cfg.known_slots
    ):
        payload = struct.pack('<4B6s2B', slot, 0, 0, 0, b"\x00" * 6, 0, 0)
        packet = build_header(DSU_motor_response, payload, protocol_version=protocol_version)
        queue_packet(packet, addr, f"motor count slot {slot} (disconnected)")
        return

    mac_address = net_cfg.slot_mac_addresses[slot]
    motor_count = state.motor_count
    payload = struct.pack(
        '<4B6sB',
        slot,
        2,  # slot state - connected
        2,  # device model - full gyro
        state.connection_type,
        mac_address,
        state.battery,
    )
    payload += struct.pack('<B', motor_count)
    packet = build_header(DSU_motor_response, payload, protocol_version=protocol_version)
    queue_packet(packet, addr, f"motor count slot {slot}")


def handle_motor_command(addr, data):
    """Update rumble motor intensity for a controller slot."""
    if len(data) < 30:
        return
    slot = data[20]
    info = net_cfg.ensure_client(addr)
    info['last_seen'] = time.time()
    info['slots'].add(slot)
    motor_id = data[28]
    intensity = data[29]
    state = controller_states.get(slot)
    if state is None or motor_id >= state.motor_count:
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
    # Neutral stick values use the centre position of 128.
    L_stick=(128, 128),
    R_stick=(128, 128),
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
    protocol_version: int | None = None,
):
    if slot >= net_cfg.soft_slot_limit:
        print("Warning: slots above 255 cannot be reported to the client")
        return
    if slot not in net_cfg.known_slots:
        if not connected:
            return
        net_cfg.known_slots.add(slot)
        for client in list(net_cfg.active_clients):
            client_info = net_cfg.active_clients.get(client, {})
            send_port_info(client, slot, protocol_version=client_info.get("protocol_version"))

    info = net_cfg.active_clients.get(addr)
    if info is None:
        return
    info['slots'].add(slot)

    counter = packet_num

    motion_ts = motion_timestamp or int(time.time() * 1000000)
    touch1 = touchpad_input1 or touchpad_input()
    touch2 = touchpad_input2 or touchpad_input()

    mac_address = net_cfg.slot_mac_addresses[slot]
    ls_x, ls_y = L_stick
    rs_x, rs_y = R_stick
    dpad_up, dpad_right, dpad_down, dpad_left = dpad_analog
    accel_x, accel_y, accel_z = accelerometer

    payload = struct.pack(
        '<4B6s2B',
        slot,
        2,  # slot state - connected
        2,  # device model - full gyro
        connection_type,
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
        ls_x,
        255 - ls_y,
        rs_x,
        255 - rs_y,
        dpad_left,
        dpad_down,
        dpad_right,
        dpad_up,
        *face_analog,
        analog_R1,
        analog_L1,
        analog_R2,
        analog_L2,
    )
    payload += struct.pack('<2B2H', *touch1)
    payload += struct.pack('<2B2H', *touch2)
    payload += struct.pack('<Q', motion_ts)
    payload += struct.pack('<6f', accel_x, accel_y, -accel_z, *gyroscope)
    packet = build_header(DSU_button_response, payload, protocol_version=protocol_version)
    queue_packet(packet, addr, f"input slot {slot}")

    prev_state = net_cfg.last_button_states.get(slot)
    current_state = (buttons1, buttons2)
    if prev_state != current_state:
        print(
            f"Sent input to {addr} slot {slot}: "
            f"buttons1=0x{buttons1:02X} buttons2=0x{buttons2:02X}"
        )
        net_cfg.last_button_states[slot] = current_state
