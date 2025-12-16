import random
import time

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
# {addr: {'last_seen': float, 'slots': set(), 'registrations': {...}}}
active_clients = {}
# Slots the server has already advertised
known_slots = set()
# Slots we have already logged input requests for
logged_pad_requests = set()
# Track last button state per slot so we only log changes
last_button_states = {}


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
    :func:`_generate_mac` with the slot number."""

    def __getitem__(self, key: int) -> bytes:
        if not isinstance(key, int) or key < 0:
            raise KeyError(key)
        val = super().get(key)
        if val is None:
            val = _generate_mac(key)
            super().__setitem__(key, val)
        return val


slot_mac_addresses = SlotMacDict({
    1: slot1_mac_address,
    2: slot2_mac_address,
    3: slot3_mac_address,
    4: slot4_mac_address,
})

# Number of unique addresses we care about. DSU slots use an unsigned 8 bit
# value which caps the protocol at 256 simultaneous controllers.  Because slot
# numbers start at 0, the highest controller number we can report is 255.
# Additional slots may exist internally but cannot be reported to a DSU client.
soft_slot_limit = 256

# Track which warnings have been printed so they only show once
_warned_messages: set[str] = set()


def _warn_once(msg: str) -> None:
    """Print ``msg`` if it has not been shown before."""
    if msg not in _warned_messages:
        print(msg)
        _warned_messages.add(msg)


def _generate_mac(idx: int) -> bytes:
    """Return a MAC address for ``idx``."""
    mac_int = idx % soft_slot_limit
    if idx >= soft_slot_limit:
        _warn_once("Warning: slots above 255 cannot be reported to the client")
    return mac_int.to_bytes(6, 'big')


def ensure_slot(slot: int) -> None:
    """Ensure ``slot_mac_addresses`` has an entry for ``slot``."""
    if slot < 0:
        raise ValueError("slot index cannot be negative")
    slot_mac_addresses[slot]


def ensure_slot_count(n: int) -> None:
    """Generate addresses for slots ``1`` through ``n`` and slot ``0``.

    Also prints warnings for unusual slot counts."""
    if n > 4:
        _warn_once("Warning: more than four controllers is non-standard but supported.")
    if n > soft_slot_limit:
        _warn_once(
            "Warning: more than 256 controllers configured; only slots up to 255 can be reported"
        )
    if n > (1 << 48):
        _warn_once(
            "Warning: you are insane; MAC addresses will be truncated for slots above 2^48"
        )
    ensure_slot(0)
    for i in range(1, n + 1):
        ensure_slot(i)


def _registration_defaults() -> dict:
    """Return an empty registration tracking structure."""
    return {'all': 0.0, 'slots': {}, 'macs': {}}


def ensure_client(addr) -> dict:
    """Return client info for ``addr``, creating defaults if needed."""
    info = active_clients.setdefault(addr, {'last_seen': time.time(), 'slots': set()})
    info.setdefault('last_seen', time.time())
    info.setdefault('slots', set())
    info.setdefault('registrations', _registration_defaults())
    return info
