import time
import socket
import threading
import argparse
import os
import select

import libraries.net_config as net_cfg
from libraries.masks import ControllerState, ControllerStateDict
from libraries.inputs import load_controller_loop
from protocols.dsu import DSUProtocol

# Sentinel used when a controller slot should remain idle but be treated as
# connected by the server.
IDLE = object()


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


def parse_update_timeout(value: str | float | int | None) -> float | None:
    """Parse an update timeout value allowing ``"none"`` to disable it."""

    if value is None:
        return None

    if isinstance(value, (int, float)):
        value = str(value)

    lowered = value.lower()
    if lowered in {"none", "null"}:
        return None

    timeout = float(value)
    if timeout < 0:
        raise argparse.ArgumentTypeError("update timeout must be non-negative")
    return timeout


def parse_arguments():
    """Return parsed CLI arguments."""
    parser = argparse.ArgumentParser(description="DSUwU - Server")
    parser.add_argument("--port", type=int, help="UDP port to listen on")
    parser.add_argument("--server-id", dest="server_id",
                        type=parse_server_id,
                        help="Server identifier (hex)")
    parser.add_argument(
        "--update-timeout",
        dest="update_timeout",
        type=parse_update_timeout,
        default=0.005,
        help=(
            "Seconds to wait between state updates; set to 0 or none to only "
            "dispatch when input changes."
        ),
    )
    for i in range(0, 5):
        parser.add_argument(
            f"--controller{i}-script",
            dest=f"controller{i}_script",
            help=f"Path to controller {i} script",
        )

    args, unknown = parser.parse_known_args()

    # Collect script paths for any --controllerN-script option
    script_map = {i: getattr(args, f"controller{i}_script") for i in range(0, 5)}
    # Convert special strings so slots can be initialized without a controller
    # thread or marked as always connected.
    for slot, path in list(script_map.items()):
        if isinstance(path, str):
            lowered = path.lower()
            if lowered == "none":
                script_map[slot] = None
            elif lowered == "idle":
                script_map[slot] = IDLE
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
                if isinstance(path, str):
                    lowered = path.lower()
                    if lowered == "none":
                        path = None
                    elif lowered == "idle":
                        path = IDLE
                script_map[slot] = path
            else:
                parser.error(f"Invalid option {opt}")
        else:
            parser.error(f"Unrecognized argument {opt}")
        i += 1

    # Normalize any special strings that may have come from dynamic options
    for slot, path in list(script_map.items()):
        if isinstance(path, str):
            lowered = path.lower()
            if lowered == "none":
                script_map[slot] = None
            elif lowered == "idle":
                script_map[slot] = IDLE

    start_slot = 0
    max_slot = max(script_map)
    scripts = [script_map.get(i) for i in range(start_slot, max_slot + 1)]
    args.controller_scripts = scripts
    args.slot_count = len(scripts)
    args.start_slot = start_slot
    return args


def start_server(port: int = net_cfg.UDP_port,
                 server_id_value: int | None = None,
                 scripts: list | None = None,
                 start_slot: int = 0,
                 update_timeout: float | None = 0.005,
                 protocol_cls: type[DSUProtocol] = DSUProtocol):
    """Launch the DSUwU - Server in a background thread.

    Returns a tuple of ``(controller_states, stop_event, thread)`` so callers
    can update controller state or stop the server when done.
    """

    if scripts is not None:
        converted = []
        for s in scripts:
            if isinstance(s, str):
                lowered = s.lower()
                if lowered == "none":
                    converted.append(None)
                    continue
                if lowered == "idle":
                    converted.append(IDLE)
                    continue
            converted.append(s)
        scripts = converted
    slot_count = len(scripts) if scripts is not None else 4 + (start_slot == 0)
    max_slot = start_slot + slot_count - 1
    net_cfg.ensure_slot_count(max_slot)

    slot_range = range(start_slot, max_slot + 1)
    notify_r, notify_w = socket.socketpair()
    notify_r.setblocking(False)
    notify_w.setblocking(False)

    class NotifyingEvent(threading.Event):
        def set(self):
            already_set = self.is_set()
            result = super().set()
            if not already_set:
                try:
                    notify_w.send(b"\0")
                except BlockingIOError:
                    pass
            return result

    state_dirty = NotifyingEvent()
    controller_states = ControllerStateDict({slot: ControllerState(connected=False) for slot in slot_range})
    controller_states._dirty_event = state_dirty
    for state in controller_states.values():
        state._dirty_event = state_dirty
    stop_event = threading.Event()

    def _thread_main() -> None:
        nonlocal port, server_id_value, scripts, slot_count, start_slot

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind((net_cfg.UDP_IP, port))
        sock.setblocking(False)

        protocol = protocol_cls(server_id_value)

        script_dir = os.path.dirname(__file__)
        default_scripts = []
        if start_slot == 0:
            default_scripts.append(None)
        default_scripts.extend([
            os.path.join(script_dir, "demo", "circle_loop.py"),
            os.path.join(script_dir, "demo", "cross_loop.py"),
            os.path.join(script_dir, "demo", "square_loop.py"),
            os.path.join(script_dir, "demo", "triangle_loop.py"),
        ])

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

        idle_slots = {start_slot + i for i, sp in enumerate(use_scripts) if sp is IDLE}

        net_cfg.known_slots.clear()
        net_cfg.known_slots.update(idle_slots)
        for slot in list(controller_states):
            controller_states[slot].connected = slot in idle_slots

        controller_threads: list[threading.Thread] = []
        for slot in list(controller_states):
            script_path = use_scripts[slot - start_slot]
            if script_path is None or script_path is IDLE:
                continue
            loop_func = load_controller_loop(script_path)
            t = threading.Thread(
                target=loop_func,
                args=(stop_event, controller_states, slot),
                daemon=True,
            )
            t.start()
            controller_threads.append(t)

        protocol.initialize(sock, controller_states, stop_event, idle_slots)

        try:
            wait_timeout = None if update_timeout in (None, 0) else update_timeout
            while not stop_event.is_set():
                readable, _, _ = select.select([sock, notify_r], [], [], wait_timeout)

                if stop_event.is_set():
                    break

                if sock in readable:
                    protocol.handle_requests(sock)

                dirty_triggered = notify_r in readable
                if dirty_triggered:
                    try:
                        while notify_r.recv(1024):
                            pass
                    except BlockingIOError:
                        pass

                timed_update = wait_timeout is not None and not readable
                if dirty_triggered or state_dirty.is_set() or timed_update:
                    protocol.update_clients(controller_states)
                    state_dirty.clear()
        except Exception as exc:
            print(f"Server loop crashed: {exc}")
        finally:
            stop_event.set()
            for t in controller_threads:
                t.join()
            protocol.shutdown()
            sock.close()
            notify_r.close()
            notify_w.close()

    thread = threading.Thread(target=_thread_main, daemon=True)
    thread.start()
    return controller_states, stop_event, thread



if __name__ == "__main__":
    args = parse_arguments()
    scripts = args.controller_scripts
    if not any(scripts):
        scripts = None

    controller_states, stop_event, thread = start_server(
        port=args.port or net_cfg.UDP_port,
        server_id_value=args.server_id,
        scripts=scripts,
        start_slot=args.start_slot,
        update_timeout=args.update_timeout,
    )

    try:
        thread.join()
    except KeyboardInterrupt:
        print("Server shutting down.")
        stop_event.set()
        thread.join()
