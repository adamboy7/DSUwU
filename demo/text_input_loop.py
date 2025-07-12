import time
import threading
from tkinter import (
    END,
    Entry,
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
    root.minsize(320, 200)

    entry_var = StringVar()
    history: list[str] = []
    history_index = 0

    log = Text(root, height=8, width=40, state="disabled", wrap="none")
    scrollbar = Scrollbar(root, command=log.yview)
    log.configure(yscrollcommand=scrollbar.set)
    log.pack(side="left", fill="both", expand=True, padx=10, pady=(5, 0))
    scrollbar.pack(side="right", fill="y", pady=(5, 0))

    def _append_log(msg: str) -> None:
        log.configure(state="normal")
        log.insert(END, msg + "\n")
        log.see(END)
        log.configure(state="disabled")

    def _handle_entry(event=None):
        nonlocal history_index
        value = entry_var.get().strip().lower()
        entry_var.set("")
        history.append(value)
        history_index = len(history)
        _append_log(f"> {value}")
        if value in ("quit", "exit"):
            root.destroy()
            return "break"
        if value not in VALID_BUTTONS:
            _append_log(f"Unknown button: {value}")
            return "break"

        def _pulse() -> None:
            for i in range(press_duration + 1):
                if stop_event.is_set():
                    break
                pulse_button(i, controller_states, slot, **{value: True})
                time.sleep(frame_delay)

        threading.Thread(target=_pulse, daemon=True).start()
        return "break"

    Label(root, text="Enter a button name or 'quit'").pack(padx=10, pady=5)
    entry = Entry(root, textvariable=entry_var)
    entry.pack(padx=10, pady=5, fill="x")
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
