import json
import time
import threading
import logging

__all__ = ["MotionCapture"]


class MotionCapture:
    """Capture accelerometer and gyro data to a JSON lines file."""

    def __init__(self, client, interval: float = 0.01):
        """Initialize motion capture with a DSUClient and polling *interval*."""
        self.client = client
        self.interval = interval
        self.file = None
        self.thread = None
        self.stop_event = None
        self.start = None

    @property
    def active(self) -> bool:
        return self.file is not None

    def start_capture(self, path: str) -> bool:
        """Begin polling motion data and writing to *path*."""
        if self.file is not None:
            return False
        try:
            self.file = open(path, "w", encoding="utf-8")
        except OSError as exc:
            logging.error("Failed to open capture file: %s", exc)
            self.file = None
            return False
        self.start = time.time()
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()
        return True

    def stop_capture(self) -> None:
        """Stop capturing motion data."""
        if self.file is None:
            return
        if self.stop_event is not None:
            self.stop_event.set()
            if self.thread is not None:
                self.thread.join()
        try:
            self.file.close()
        finally:
            self.file = None
            self.thread = None
            self.stop_event = None
            self.start = None

    def _loop(self) -> None:
        while not self.stop_event.is_set():
            now = time.time()
            for slot, state in self.client.states.items():
                entry = {
                    "time": now - self.start,
                    "slot": slot,
                    "motion_ts": state.get("motion_ts"),
                    "accel": state.get("accel"),
                    "gyro": state.get("gyro"),
                }
                json.dump(entry, self.file)
                self.file.write("\n")
            self.file.flush()
            self.stop_event.wait(self.interval)
