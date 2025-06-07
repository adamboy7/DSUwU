from masks import *
from net_config import *

def send_input(addr, slot, buttons1=button_mask_1(), buttons2=button_mask_2(), home=False, touch_button=False, L_stick=(0,0), R_stick=(0,0), R1=False, L1=False, R2=0, L2=0, touchpad_input1=None, touchpad_input2=None):
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

        now = time.time()
        for addr in list(active_clients.keys()):
            last_seen, slot = active_clients[addr]
            if now - last_seen > 5.0:
                del active_clients[addr]
                print(f"Client {addr} timed out")
                continue

            active_clients[addr] = (now, slot)

            if frame % cycle_duration < press_duration:
                send_input(addr, slot, buttons2=button_mask_2(circle=True))
            else:
                send_input(addr, slot, buttons2=button_mask_2(circle=False))

        time.sleep(1 / 60.0)
        frame += 1

except KeyboardInterrupt:
    print("Server shutting down.")
    sock.close()
