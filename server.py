import struct
import time
import socket
import threading
import argparse
import os

from libraries.net_config import *
import libraries.net_config as net_cfg
from libraries.masks import ControllerState, ControllerStateDict
from libraries.inputs import load_controller_loop
from libraries import packet


def parse_server_id(value):
    """Parse a hex server ID ensuring it fits in 32 bits."""
    if value.lower().startswith("0x"):
        value = value[2:]
    if not value:
        raise argparse.ArgumentTypeError("server ID cannot be empty")
    if not all(c in "0123456789abcdefABCDEF" for c in value):
        raise argparse.ArgumentTypeError("server ID must be hexadecimal")
    if len(value) > 8:
        raise argparse.ArgumentTypeError("server ID must be at most 8 hex digits")
    return int(value, 16)


def parse_arguments():
    """Return parsed CLI arguments."""
    parser = argparse.ArgumentParser(description="DSUwU - Server")
    parser.add_argument("--port", type=int, help="UDP port to listen on")
    parser.add_argument("--server-id", dest="server_id",
                        type=parse_server_id,
                        help="Server identifier (hex)")
    for i in range(1, 5):
        parser.add_argument(
            f"--controller{i}-script",
            dest=f"controller{i}_script",
            help=f"Path to controller {i} script",
        )

    args, unknown = parser.parse_known_args()

    # Collect script paths for any --controllerN-script option
    script_map = {
        i: getattr(args, f"controller{i}_script") for i in range(1, 5)
    }
    # Convert literal "None" strings to actual ``None`` so slots can be
    # initialized without a controller thread.
    for slot, path in list(script_map.items()):
        if isinstance(path, str) and path.lower() == "none":
            script_map[slot] = None
    i = 0
    while i < len(unknown):
        opt = unknown[i]
        if opt.startswith("--controller") and opt.endswith("-script"):
            num = opt[len("--controller"):-len("-script")]
            if num.isdigit():
                slot = int(num)
                path = None
                if i + 1 < len(unknown) and not unknown[i + 1].startswith("--"):
                    path = unknown[i + 1]
                    i += 1
                if isinstance(path, str) and path.lower() == "none":
                    path = None
                script_map[slot] = path
            else:
                parser.error(f"Invalid option {opt}")
        else:
            parser.error(f"Unrecognized argument {opt}")
        i += 1

    # Normalize any "None" strings that may have come from dynamic options
    for slot, path in list(script_map.items()):
        if isinstance(path, str) and path.lower() == "none":
            script_map[slot] = None

    max_slot = max(script_map)
    scripts = [script_map.get(i) for i in range(1, max_slot + 1)]
    args.controller_scripts = scripts
    args.slot_count = max_slot
    return args


def start_server(port: int = UDP_port,
                 server_id_value: int | None = None,
                 scripts: list | None = None):
    """Launch the DSUwU - Server in a background thread.

    Returns a tuple of ``(controller_states, stop_event, thread)`` so callers
    can update controller state or stop the server when done.
    """

    if scripts is not None:
        scripts = [None if isinstance(s, str) and s.lower() == "none" else s
                   for s in scripts]
    slot_count = len(scripts) if scripts is not None else 4
    if slot_count > 4:
        print("Warning: more than four controller slots is non-standard but supported.")
    net_cfg.ensure_slot_count(slot_count)

    controller_states = ControllerStateDict({slot: ControllerState(connected=False) for slot in range(slot_count)})
    stop_event = threading.Event()

    def _thread_main() -> None:
        nonlocal port, server_id_value, scripts, slot_count

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind((UDP_IP, port))
        sock.setblocking(False)

        packet.start_sender(sock, stop_event)
        packet.controller_states = controller_states

        if server_id_value is not None:
            global server_id
            server_id = server_id_value
            packet.server_id = server_id_value
            net_cfg.server_id = server_id_value

        script_dir = os.path.dirname(__file__)
        default_scripts = [
            os.path.join(script_dir, "demo", "circle_loop.py"),
            os.path.join(script_dir, "demo", "cross_loop.py"),
            os.path.join(script_dir, "demo", "square_loop.py"),
            os.path.join(script_dir, "demo", "triangle_loop.py"),
        ]

        while len(default_scripts) < slot_count:
            default_scripts.append(None)

        if scripts is None:
            use_scripts = default_scripts[:slot_count]
        else:
            use_scripts = []
            for i in range(slot_count):
                if i < len(scripts):
                    use_scripts.append(scripts[i])
                else:
                    use_scripts.append(default_scripts[i])

        known_slots.clear()
        for slot in list(controller_states):
            controller_states[slot].connected = False
        prev_connection_types = {slot: controller_states[slot].connection_type for slot in controller_states}

        controller_threads: list[threading.Thread] = []
        for slot in list(controller_states):
            script_path = use_scripts[slot]
            if script_path is None:
                continue
            loop_func = load_controller_loop(script_path)
            t = threading.Thread(
                target=loop_func,
                args=(stop_event, controller_states, slot),
                daemon=True,
            )
            t.start()
            controller_threads.append(t)

        try:
            while not stop_event.is_set():
                try:
                    while True:
                        data, addr = sock.recvfrom(2048)
                        if data[:4] == b"DSUC":
                            msg_type, = struct.unpack("<I", data[16:20])
                            if msg_type == DSU_version_request:
                                packet.handle_version_request(addr)
                            elif msg_type == DSU_list_ports:
                                packet.handle_list_ports(addr, data)
                            elif msg_type == DSU_button_request:
                                packet.handle_pad_data_request(addr, data)
                            elif msg_type == DSU_motor_request:
                                packet.handle_motor_request(addr, data)
                            elif msg_type == motor_command:
                                packet.handle_motor_command(addr, data)
                except BlockingIOError:
                    pass
                except ConnectionResetError as exc:
                    print(f"Client connection reset: {exc}")
                    for client in list(active_clients):
                        print(f"Removing client {client} due to connection reset")
                    active_clients.clear()
                except Exception as exc:
                    print(f"Error processing packet: {exc}")

                now = time.time()
                for addr in list(active_clients.keys()):
                    if now - active_clients[addr]['last_seen'] > DSU_timeout:
                        del active_clients[addr]
                        print(f"Client {addr} timed out")

                for s, state in list(controller_states.items()):
                    prev_connected = state.connected
                    prev_type = prev_connection_types.get(s, state.connection_type)
                    state.update_connection(stick_deadzone)

                    if state.connection_type != prev_type:
                        prev_connection_types[s] = state.connection_type
                        if state.connection_type == -1:
                            state.connected = False
                            known_slots.discard(s)
                            for client in list(active_clients):
                                packet.send_port_disconnect(client, s)
                        else:
                            known_slots.add(s)
                            for client in list(active_clients):
                                packet.send_port_info(client, s)

                    if (
                        state.connection_type != -1
                        and not prev_connected
                        and state.connected
                        and s not in known_slots
                    ):
                        known_slots.add(s)
                        for client in list(active_clients):
                            packet.send_port_info(client, s)
                    if state.connection_type != -1:
                        for addr in list(active_clients):
                            packet.send_input(
                                addr,
                                s,
                                connected=state.connected,
                                packet_num=state.packet_num,
                                buttons1=state.buttons1,
                                buttons2=state.buttons2,
                                home=state.home,
                                touch_button=state.touch_button,
                                L_stick=state.L_stick,
                                R_stick=state.R_stick,
                                dpad_analog=state.dpad_analog,
                                face_analog=state.face_analog,
                                analog_R1=state.analog_R1,
                                analog_L1=state.analog_L1,
                                analog_R2=state.analog_R2,
                                analog_L2=state.analog_L2,
                                touchpad_input1=state.touchpad_input1,
                                touchpad_input2=state.touchpad_input2,
                                motion_timestamp=state.motion_timestamp,
                                accelerometer=state.accelerometer,
                                gyroscope=state.gyroscope,
                                connection_type=state.connection_type,
                                battery=state.battery,
                            )
                for state in list(controller_states.values()):
                    state.packet_num = (state.packet_num + 1) & 0xFFFFFFFF
                    motors = list(state.motors)
                    timestamps = list(state.motor_timestamps)
                    for i in range(state.motor_count):
                        if now - timestamps[i] > DSU_timeout and motors[i] != 0:
                            motors[i] = 0
                    state.motors = tuple(motors)
                time.sleep(0.001)
        except Exception as exc:
            print(f"Server loop crashed: {exc}")
        finally:
            stop_event.set()
            for t in controller_threads:
                t.join()
            packet.stop_sender()
            sock.close()

    thread = threading.Thread(target=_thread_main, daemon=True)
    thread.start()
    return controller_states, stop_event, thread



if __name__ == "__main__":
    args = parse_arguments()
    scripts = args.controller_scripts
    if not any(scripts):
        scripts = None

    controller_states, stop_event, thread = start_server(
        port=args.port or UDP_port,
        server_id_value=args.server_id,
        scripts=scripts,
    )

    try:
        thread.join()
    except KeyboardInterrupt:
        print("Server shutting down.")
        stop_event.set()
        thread.join()
