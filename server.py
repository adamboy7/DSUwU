from masks import *
from net_config import *

# Store the last known input state per slot
controller_states = {
    slot: {
        "buttons1": button_mask_1(),
        "buttons2": button_mask_2(),
        "home": False,
        "touch_button": False,
        "L_stick": (0, 0),
        "R_stick": (0, 0),
        "R1": False,
        "L1": False,
        "R2": 0,
        "L2": 0,
        "touchpad_input1": None,
        "touchpad_input2": None
    } for slot in range(4)
}

def send_input(addr, slot, buttons1=button_mask_1(), buttons2=button_mask_2(), home=False, touch_button=False, L_stick=(0,0), R_stick=(0,0), R1=False, L1=False, R2=0, L2=0, touchpad_input1=None, touchpad_input2=None):
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
        *touch1,  #Active, ID, X, Y
        *touch2,  #Active, ID, X, Y
        timestamp_us,
        0.0, 0.0, 0.0, 0.0, 0.0, 0.0
    )
    packet = build_header(DSU_PAD_DATA_RESPONSE, payload)
    sock.sendto(packet, addr)
    print(f"Sent input to {addr} slot {slot}")

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

    # Send all controller states to this address
    for s, state in controller_states.items():
        send_input(addr, s, **state)

frame = 0
press_duration = 3
cycle_duration = 60

try:
    while True:
        try:
            data, addr = sock.recvfrom(2048)
            if data[:4] == b'DSUC':
                msg_type, = struct.unpack('<I', data[16:20])
                if msg_type == DSU_VERSION_REQUEST:
                    handle_version_request(addr)
                elif msg_type == DSU_LIST_PORTS:
                    handle_list_ports(addr, data)
                elif msg_type == DSU_PAD_DATA_REQUEST:
                    handle_pad_data_request(addr, data)
        except BlockingIOError:
            pass

        # Update controller states
        for slot in range(4):
            if frame % cycle_duration < press_duration:
                controller_states[slot]["buttons2"] = button_mask_2(
                    circle=(slot == 0),
                    cross=(slot == 1),
                    square=(slot == 2),
                    triangle=(slot == 3)
                )
            else:
                controller_states[slot]["buttons2"] = button_mask_2()

        # Clean up stale clients
        now = time.time()
        for addr in list(active_clients.keys()):
            if now - active_clients[addr]['last_seen'] > 5.0:
                del active_clients[addr]
                client_port_info.pop(addr, None)
                print(f"Client {addr} timed out")

        time.sleep(1 / 60.0)
        frame += 1

except KeyboardInterrupt:
    print("Server shutting down.")
    sock.close()
