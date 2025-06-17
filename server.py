import struct
import time
import socket
import threading
import argparse
import os

from libraries.net_config import *
from libraries.masks import *
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
    parser = argparse.ArgumentParser(description="DSU server")
    parser.add_argument("--port", type=int, help="UDP port to listen on")
    parser.add_argument("--server-id", dest="server_id",
                        type=parse_server_id,
                        help="Server identifier (hex)")
    parser.add_argument(
        "--controller1-script",
        dest="controller1_script",
        help="Path to controller 1 script",
    )
    parser.add_argument(
        "--controller2-script",
        dest="controller2_script",
        help="Path to controller 2 script",
    )
    parser.add_argument(
        "--controller3-script",
        dest="controller3_script",
        help="Path to controller 3 script",
    )
    parser.add_argument(
        "--controller4-script",
        dest="controller4_script",
        help="Path to controller 4 script",
    )
    return parser.parse_args()


def start_server(port: int = UDP_port,
                 server_id_value: int | None = None,
                 scripts: list | None = None):
    """Launch the DSU server in a background thread.

    Returns a tuple of ``(controller_states, stop_event, thread)`` so callers
    can update controller state or stop the server when done.
    """

    controller_states = {slot: ControllerState() for slot in range(4)}
    stop_event = threading.Event()

    def _thread_main() -> None:
        nonlocal port, server_id_value, scripts

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind((UDP_IP, port))
        sock.setblocking(False)

        packet.sock = sock
        packet.controller_states = controller_states

        if server_id_value is not None:
            global server_id
            server_id = server_id_value
            packet.server_id = server_id_value

        script_dir = os.path.dirname(__file__)
        default_scripts = [
            os.path.join(script_dir, "demo", "circle_loop.py"),
            os.path.join(script_dir, "demo", "cross_loop.py"),
            os.path.join(script_dir, "demo", "square_loop.py"),
            os.path.join(script_dir, "demo", "triangle_loop.py"),
        ]

        if scripts is None:
            use_scripts = [None] * 4
        else:
            use_scripts = []
            for i in range(4):
                if i < len(scripts):
                    path = scripts[i]
                    if path is None:
                        use_scripts.append(None)
                    else:
                        use_scripts.append(path)
                else:
                    use_scripts.append(default_scripts[i])

        controller_threads: list[threading.Thread] = []
        for slot in controller_states:
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

                now = time.time()
                for addr in list(active_clients.keys()):
                    if now - active_clients[addr]['last_seen'] > DSU_timeout:
                        del active_clients[addr]
                        print(f"Client {addr} timed out")

                for addr in active_clients:
                    for s, state in controller_states.items():
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
                        )
                for state in controller_states.values():
                    state.packet_num = (state.packet_num + 1) & 0xFFFFFFFF
                    motors = list(state.motors)
                    timestamps = list(state.motor_timestamps)
                    for i in range(len(motors)):
                        if now - timestamps[i] > DSU_timeout and motors[i] != 0:
                            motors[i] = 0
                    state.motors = tuple(motors)
                time.sleep(0.001)
        finally:
            stop_event.set()
            for t in controller_threads:
                t.join()
            sock.close()

    thread = threading.Thread(target=_thread_main, daemon=True)
    thread.start()
    return controller_states, stop_event, thread



if __name__ == "__main__":
    args = parse_arguments()

    scripts = [
        args.controller1_script,
        args.controller2_script,
        args.controller3_script,
        args.controller4_script,
    ]
    if not any(scripts):
        script_dir = os.path.dirname(__file__)
        scripts = [
            os.path.join(script_dir, "demo", "circle_loop.py"),
            os.path.join(script_dir, "demo", "cross_loop.py"),
            os.path.join(script_dir, "demo", "square_loop.py"),
            os.path.join(script_dir, "demo", "triangle_loop.py"),
        ]

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
