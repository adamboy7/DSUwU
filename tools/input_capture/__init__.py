import json
import time
import logging

__all__ = ["InputCapture"]


class InputCapture:
    """Capture DSU input states to a JSON lines file."""

    def __init__(self, client):
        self.client = client
        self.file = None
        self.start = None
        self.last_logged = {}

    @property
    def active(self) -> bool:
        return self.file is not None

    def start_capture(self, path: str) -> bool:
        """Start logging controller input to *path*."""
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
        self.client.state_callback = self._capture_state
        return True

    def stop_capture(self) -> None:
        """Stop logging and close the capture file."""
        if self.file is None:
            return
        try:
            self.file.close()
        finally:
            self.file = None
        self.start = None
        self.client.state_callback = None

    def _capture_state(self, slot: int, state: dict) -> None:
        if self.file is None or self.start is None:
            return
        relevant = {
            "connected": state["connected"],
            "buttons1": state["buttons1"],
            "buttons2": state["buttons2"],
            "home": state["home"],
            "touch_button": state["touch_button"],
            "ls": state["ls"],
            "rs": state["rs"],
            "dpad": state["dpad"],
            "face": state["face"],
            "analog_r1": state["analog_r1"],
            "analog_l1": state["analog_l1"],
            "analog_r2": state["analog_r2"],
            "analog_l2": state["analog_l2"],
            "touch1": state["touch1"],
            "touch2": state["touch2"],
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
