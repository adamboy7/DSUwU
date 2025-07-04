import random

# Server config
UDP_IP = "0.0.0.0"
UDP_port = 26760
DSU_timeout = 5.0
# Tolerance for analog stick drift when detecting connection status
stick_deadzone = 3

# Number of rumble motors supported per controller
motor_count = 2

# Server state tracking
server_id = random.randint(0, 0xFFFFFFFF)
# {addr: {'last_seen': float, 'slots': set()}}
active_clients = {}
# Slots the server has already advertised
known_slots = {0}
# Slots we have already logged input requests for
logged_pad_requests = set()
# Track last button state per slot so we only log changes
last_button_states = {}

# DSU Message Types
DSU_version_request  = 0x100000
DSU_version_response = 0x100000
DSU_list_ports       = 0x100001
DSU_port_info        = 0x100001
DSU_button_request = 0x100002
DSU_button_response = 0x100002
DSU_motor_request = 0x110001
DSU_motor_response = 0x110001
motor_command = 0x110002

PROTOCOL_VERSION = 1001

# Unique MAC addresses per controller slot. Entries may be ``None`` to have
# an address generated automatically.  The first four slots keep the previous
# default values as examples of manual assignment.
slot1_mac_address = b'\xAA\xBB\xCC\xDD\xEE\x01'
slot2_mac_address = b'\xAA\xBB\xCC\xDD\xEE\x02'
slot3_mac_address = b'\xAA\xBB\xCC\xDD\xEE\x03'
slot4_mac_address = b'\xAA\xBB\xCC\xDD\xEE\x04'

class SlotMacDict(dict):
    """Dictionary mapping slot numbers to MAC addresses.

    Missing or ``None`` entries are filled automatically using
    :func:`_generate_mac` with the slot's 1-based index."""

    def __getitem__(self, key: int) -> bytes:
        if not isinstance(key, int) or key < 0:
            raise KeyError(key)
        val = super().get(key)
        if val is None:
            val = _generate_mac(key + 1)
            super().__setitem__(key, val)
        return val


slot_mac_addresses = SlotMacDict({
    0: slot1_mac_address,
    1: slot2_mac_address,
    2: slot3_mac_address,
    3: slot4_mac_address,
})

# Number of unique addresses available (48 bits)
_MAC_LIMIT = 1 << 48
_mac_wrap_warned = False


def _generate_mac(idx: int) -> bytes:
    """Return a MAC address for ``idx`` (1-based)."""
    global _mac_wrap_warned
    mac_int = idx % _MAC_LIMIT
    if idx >= _MAC_LIMIT and not _mac_wrap_warned:
        print("Warning: more than 2^48 slots requested; MAC addresses will be"
              " recycled. Are you insane?")
        _mac_wrap_warned = True
    return mac_int.to_bytes(6, 'big')


def ensure_slot(slot: int) -> None:
    """Ensure ``slot_mac_addresses`` has an entry for ``slot``."""
    if slot < 0:
        raise ValueError("slot index cannot be negative")
    slot_mac_addresses[slot]


def ensure_slot_count(n: int) -> None:
    """Generate addresses for slots ``0`` through ``n - 1``."""
    for i in range(n):
        ensure_slot(i)

