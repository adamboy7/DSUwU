import time
import os
import sys
import multiprocessing as mp
from queue import Empty

from libraries.inputs import (
    frame_delay,
    pulse_button,
    press_duration,
    VALID_BUTTONS,
)


def _set_console_title(slot: int) -> None:
    """Attempt to update the current console title."""
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.kernel32.SetConsoleTitleW(f"Controller {slot}")
        except Exception:
            pass
    else:
        sys.stdout.write(f"\33]0;Controller {slot}\a")
        sys.stdout.flush()


def _input_worker(slot: int, q: mp.Queue):
    """Prompt for button names and forward them on ``q``."""

    if os.name == "nt":
        # Detach from the parent's console and open a new one
        try:
            import ctypes

            ctypes.windll.kernel32.FreeConsole()
            ctypes.windll.kernel32.AllocConsole()
        except Exception:
            pass

    _set_console_title(slot)

    print("Text input controller: enter a button name or 'quit' to exit")
    while True:
        try:
            entry = input("Button> ").strip().lower()
        except EOFError:
            q.put(None)
            break
        if entry.startswith("/slot"):
            parts = entry.split(maxsplit=1)
            if len(parts) == 2:
                try:
                    slot = int(parts[1])
                    _set_console_title(slot)
                except ValueError:
                    pass

        q.put(entry)
        if entry in {"quit", "exit"}:
            break


def controller_loop(stop_event, controller_states, slot):
    """Prompt for button names in a separate console and pulse them."""

    queue: mp.Queue[str | None] = mp.Queue()
    proc = mp.Process(target=_input_worker, args=(slot, queue), daemon=True)
    proc.start()

    hold_frames = press_duration
    cur_slot = slot

    while not stop_event.is_set():
        try:
            entry = queue.get(timeout=0.1)
        except Empty:
            if not proc.is_alive():
                break
            continue

        if entry is None or entry in {"quit", "exit"}:
            break

        if entry.startswith("/frames"):
            parts = entry.split(maxsplit=1)
            if len(parts) == 2:
                try:
                    hold_frames = max(1, int(parts[1]))
                    print(f"Frame hold set to {hold_frames}")
                except ValueError:
                    print("Invalid frame count")
            else:
                print("Usage: /frames <count>")
            continue

        if entry.startswith("/slot"):
            parts = entry.split(maxsplit=1)
            if len(parts) == 2:
                try:
                    cur_slot = int(parts[1])
                    print(f"Controller slot set to {cur_slot}")
                except ValueError:
                    print("Invalid slot number")
            else:
                print("Usage: /slot <slot>")
            continue

        if entry not in VALID_BUTTONS:
            print(f"Unknown button: {entry}")
            continue

        for i in range(hold_frames + 1):
            if stop_event.is_set():
                break
            pulse_button(i, controller_states, cur_slot, **{entry: True})
            time.sleep(frame_delay)

    if proc.is_alive():
        proc.terminate()
        proc.join()
