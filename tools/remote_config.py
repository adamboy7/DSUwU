"""Shared configuration for DSUwU remote play clients.

Edit this file to configure the target server. Settings here apply to both
``tools/remote_client.py`` (pygame) and ``tools/remote_client_hid.py`` (HID).
"""

# IP address of the DSUwU server machine (the one running remote_input_script.py).
SERVER_IP = "127.0.0.1"

# UDP port to send packets to. Must match LISTEN_PORT in remote_input_script.py.
SERVER_PORT = 26761

# Controller slot to emulate on the server (1–4 for standard DSU).
SLOT = 1
