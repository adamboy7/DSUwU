import json
import time
import logging

__all__ = ["MotionCapture"]


class MotionCapture:
    """Capture accelerometer and gyro data to a JSON lines file."""

    def __init__(self, client):
        """Initialize motion capture bound to *client*."""
        self.client = client
        self.file = None
        self.start = None
        self.last_logged = {}
        self._prev_callback = None

    @property
    def active(self) -> bool:
        return self.file is not None

    def start_capture(self, path: str) -> bool:
        """Start recording motion data to *path*."""
        if self.file is not None:
            return False
        try:
            self.file = open(path, "w", encoding="utf-8")
        except OSError as exc:
            logging.error("Failed to open capture file: %s", exc)
            self.file = None
            return False
        self.start = time.time()
        self.last_logged.clear()
        self._prev_callback = self.client.state_callback

        def wrapper(slot: int, state: dict) -> None:
            if self._prev_callback is not None:
                try:
                    self._prev_callback(slot, state)
                except Exception as exc:  # pragma: no cover - just in case
                    logging.error("State callback failed: %s", exc)
            self._capture_state(slot, state)

        self.client.state_callback = wrapper
        return True

    def stop_capture(self) -> None:
        """Stop recording and close the capture file."""
        if self.file is None:
            return
        try:
            self.file.close()
        finally:
            self.file = None
        self.start = None
        self.client.state_callback = self._prev_callback
        self._prev_callback = None

    def _capture_state(self, slot: int, state: dict) -> None:
        if self.file is None or self.start is None:
            return
        relevant = {
            "motion_ts": state.get("motion_ts"),
            "accel": state.get("accel"),
            "gyro": state.get("gyro"),
        }
        prev = self.last_logged.get(slot)
        if prev == relevant:
            return
        self.last_logged[slot] = relevant
        entry = {
            "time": time.time() - self.start,
            "slot": slot,
            **relevant,
        }
        json.dump(entry, self.file)
        self.file.write("\n")
        self.file.flush()
