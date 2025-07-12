import time
import threading
from tkinter import Entry, Label, StringVar, Tk

from libraries.inputs import (
    frame_delay,
    pulse_button,
    press_duration,
    VALID_BUTTONS,
)


def controller_loop(stop_event, controller_states, slot):
    """Open a small window for entering button names."""

    root = Tk()
    root.title(f"Text input controller (slot {slot})")

    entry_var = StringVar()

    def _handle_entry(event=None):
        value = entry_var.get().strip().lower()
        entry_var.set("")
        if value in ("quit", "exit"):
            root.destroy()
            return
        if value not in VALID_BUTTONS:
            print(f"Unknown button: {value}")
            return

        def _pulse() -> None:
            for i in range(press_duration + 1):
                if stop_event.is_set():
                    break
                pulse_button(i, controller_states, slot, **{value: True})
                time.sleep(frame_delay)

        threading.Thread(target=_pulse, daemon=True).start()

    Label(root, text="Enter a button name or 'quit'").pack(padx=10, pady=5)
    entry = Entry(root, textvariable=entry_var)
    entry.pack(padx=10, pady=5)
    entry.bind("<Return>", _handle_entry)
    entry.focus()

    def _check_stop():
        if stop_event.is_set():
            root.destroy()
        else:
            root.after(100, _check_stop)

    root.after(100, _check_stop)
    root.mainloop()
