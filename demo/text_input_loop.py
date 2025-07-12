import time

from libraries.inputs import (
    frame_delay,
    pulse_button,
    press_duration,
    VALID_BUTTONS,
)


def controller_loop(stop_event, controller_states, slot):
    """Prompt for button names and pulse them when entered."""

    print("Text input controller: enter a button name or 'quit' to exit")
    while not stop_event.is_set():
        try:
            entry = input("Button> ").strip().lower()
        except EOFError:
            break
        if entry == "quit" or entry == "exit":
            break
        if entry not in VALID_BUTTONS:
            print(f"Unknown button: {entry}")
            continue
        for i in range(press_duration + 1):
            if stop_event.is_set():
                break
            pulse_button(i, controller_states, slot, **{entry: True})
            time.sleep(frame_delay)
