import time
import types

from libraries.inputs import frame_delay
from libraries import net_config as net_cfg


def _noop_update(self, dz=net_cfg.stick_deadzone):
    """Replacement ``update_connection`` method that preserves ``connected``."""
    pass


def controller_loop(stop_event, controller_states, slot):
    """Mark the first eight controller slots as always connected."""

    for s in range(8):
        state = controller_states[s]
        state.connection_type = 2  # Default to Bluetooth
        state.connected = True
        state.update_connection = types.MethodType(_noop_update, state)

    while not stop_event.is_set():
        time.sleep(frame_delay)
