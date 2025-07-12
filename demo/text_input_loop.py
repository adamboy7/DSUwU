import time
import threading
from tkinter import (
    END,
    Entry,
    Frame,
    Label,
    Scrollbar,
    StringVar,
    Text,
    Tk,
)

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
    root.minsize(400, 250)

    hold_frames = press_duration
    active_slot = slot

    entry_var = StringVar()
    history: list[str] = []
    history_index = 0

    frame = Frame(root)
    frame.pack(side="top", fill="both", expand=True, padx=10, pady=(5, 0))

    log = Text(frame, height=8, state="disabled", wrap="none")
    scrollbar = Scrollbar(frame, command=log.yview)
    log.configure(yscrollcommand=scrollbar.set)

    log.grid(row=0, column=0, sticky="nsew")
    scrollbar.grid(row=0, column=1, sticky="ns")

    frame.columnconfigure(0, weight=1)
    frame.rowconfigure(0, weight=1)

    def _append_log(msg: str) -> None:
        log.configure(state="normal")
        log.insert(END, msg + "\n")
        log.see(END)
        log.configure(state="disabled")

    def _handle_entry(event=None):
        nonlocal history_index, hold_frames, active_slot
        value = entry_var.get().strip().lower()
        entry_var.set("")
        history.append(value)
        history_index = len(history)
        _append_log(f"> {value}")

        parts = value.split()
        if not parts:
            return "break"
        cmd = parts[0]

        if cmd in ("quit", "exit"):
            root.destroy()
            return "break"

        if cmd == "/frames":
            if len(parts) == 2 and parts[1].isdigit():
                hold_frames = int(parts[1])
                _append_log(f"Frame hold set to {hold_frames}")
            else:
                _append_log("Usage: /frames NUMBER")
            return "break"

        if cmd == "/slot":
            if len(parts) == 2 and parts[1].isdigit():
                active_slot = int(parts[1])
                root.title(f"Text input controller (slot {active_slot})")
                _append_log(f"Using slot {active_slot}")
            else:
                _append_log("Usage: /slot NUMBER")
            return "break"

        if cmd not in VALID_BUTTONS:
            _append_log(f"Unknown button: {cmd}")
            return "break"

        button = cmd

        def _pulse() -> None:
            for i in range(hold_frames + 1):
                if stop_event.is_set():
                    break
                pulse_button(i, controller_states, active_slot, **{button: True})
                time.sleep(frame_delay)

        threading.Thread(target=_pulse, daemon=True).start()
        return "break"

    bottom = Frame(root)
    bottom.pack(side="bottom", fill="x", padx=10, pady=5)

    Label(bottom, text="Enter a button name or 'quit'").pack(anchor="w")
    entry = Entry(bottom, textvariable=entry_var)
    entry.pack(fill="x", pady=(2, 0))
    entry.bind("<Return>", _handle_entry)

    def _show_prev(event):
        nonlocal history_index
        if not history:
            return "break"
        if history_index > 0:
            history_index -= 1
        entry_var.set(history[history_index])
        entry.icursor(END)
        return "break"

    def _show_next(event):
        nonlocal history_index
        if not history:
            return "break"
        if history_index < len(history):
            history_index += 1
        if history_index == len(history):
            entry_var.set("")
        else:
            entry_var.set(history[history_index])
        entry.icursor(END)
        return "break"

    entry.bind("<Up>", _show_prev)
    entry.bind("<Down>", _show_next)
    entry.focus()

    def _check_stop():
        if stop_event.is_set():
            root.destroy()
        else:
            root.after(100, _check_stop)

    root.after(100, _check_stop)
    root.mainloop()
