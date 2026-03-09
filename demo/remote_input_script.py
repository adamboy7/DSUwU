"""Controller script that receives remote player input over UDP.

This script acts as a controller source for the DSUwU server. It opens a
listening UDP socket and applies incoming DSU button response packets — sent
by ``demo/remote_client.py`` on the player's machine — to the local
controller state. The emulator then reads that state from the DSU server as
normal.

The remote player only needs outbound network access. This machine (the
server) needs UDP ``LISTEN_PORT`` reachable from the internet (e.g. via
port forwarding).

Usage (as a controller script):
    python server.py --controller1-script demo/remote_input_script.py

Configuration:
- LISTEN_PORT: UDP port to listen on. Must match SERVER_PORT in
  remote_client.py (default 26761).
- ALLOWED_IPS: Optional set of IP addresses to accept packets from.
  Leave empty to accept from any address.
- TIMEOUT: Seconds without a packet before the slot is marked
  disconnected. Set to 0 to disable the timeout.
"""

import select
import socket
import time

from demo.dsu_forward_client import _copy_state, parse_button_response

# ---------------------------------------------------------------------------
# Configuration — edit these before running
# ---------------------------------------------------------------------------

# UDP port to listen on (must match SERVER_PORT in remote_client.py).
LISTEN_PORT = 26761

# Restrict incoming packets to these IP addresses. Leave empty to allow all.
ALLOWED_IPS: set[str] = set()

# Mark the controller disconnected after this many seconds with no packet.
# Set to 0 to disable (last received state persists indefinitely).
TIMEOUT = 3.0


# ---------------------------------------------------------------------------
# Controller loop
# ---------------------------------------------------------------------------

def controller_loop(stop_event, controller_states, slot) -> None:
    """Receive remote input packets and apply them to ``controller_states``."""

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", LISTEN_PORT))
    sock.setblocking(False)

    print(f"Remote input receiver: listening on UDP port {LISTEN_PORT} (slot {slot})")
    if ALLOWED_IPS:
        print(f"  Accepting only from: {', '.join(sorted(ALLOWED_IPS))}")
    if TIMEOUT > 0:
        print(f"  Disconnect timeout: {TIMEOUT}s")

    last_packet_time = 0.0
    timed_out = False

    while not stop_event.is_set():
        readable, _, _ = select.select([sock], [], [], 0.1)

        if readable:
            try:
                data, addr = sock.recvfrom(2048)
            except OSError:
                continue

            if ALLOWED_IPS and addr[0] not in ALLOWED_IPS:
                continue

            state = parse_button_response(data)
            if state is None:
                continue

            _copy_state(slot, controller_states, state)
            last_packet_time = time.monotonic()
            timed_out = False

        elif TIMEOUT > 0 and last_packet_time > 0 and not timed_out:
            if time.monotonic() - last_packet_time > TIMEOUT:
                controller = controller_states[slot]
                controller.connected = False
                timed_out = True
                print(f"Remote input receiver: client timed out on slot {slot}")

    sock.close()
