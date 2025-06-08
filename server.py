from net_config import *

frame = 0
press_duration = 3
cycle_duration = 60

# Store the last known input state per slot
controller_states = {slot: ControllerState() for slot in range(4)}

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
                controller_states[slot].buttons2 = button_mask_2(
                    circle=(slot == 0),
                    cross=(slot == 1),
                    square=(slot == 2),
                    triangle=(slot == 3)
                )
            else:
                controller_states[slot].buttons2 = button_mask_2()

        # Clean up stale clients
        now = time.time()
        for addr in list(active_clients.keys()):
            if now - active_clients[addr]['last_seen'] > 5.0:
                del active_clients[addr]
                client_port_info.pop(addr, None)
                print(f"Client {addr} timed out")
        # Send all controller states to all active clients
        for addr in active_clients:
            for s, state in controller_states.items():
                send_input(addr, s, **vars(state))

        time.sleep(1 / 60.0)
        frame += 1

except KeyboardInterrupt:
    print("Server shutting down.")
    sock.close()
