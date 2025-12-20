"""Compare circle button updates between two controller slots.

This script observes slots 1 and 2 and reports which slot delivers a circle
button change first, including the latency delta between the updates. It can
be used to compare the timing of different input sources (for example,
DS4-HID vs. pygame) that read from the same controller. After detecting
changes from both slots, the script resets and waits for the next event pair.
"""

import time

from libraries.inputs import frame_delay
from libraries.masks import button_mask_2


CIRCLE_MASK = button_mask_2(circle=True)


def _circle_active(controller_states, slot: int) -> bool:
    """Return ``True`` if the circle bit is set for ``slot``."""

    return bool(controller_states[slot].buttons2 & CIRCLE_MASK)


def controller_loop(stop_event, controller_states, slot):
    """Monitor circle button timing between slots 1 and 2."""

    print(
        "circle_race: watching circle button updates on slots 1 and 2 to compare latency"
    )

    previous_circle = {
        1: _circle_active(controller_states, 1),
        2: _circle_active(controller_states, 2),
    }
    last_change_time = {1: None, 2: None}

    comparison_count = 0
    simultaneous_threshold = 0.0001  # 0.1 ms

    while not stop_event.is_set():
        now = time.monotonic()
        circle_state = {
            1: _circle_active(controller_states, 1),
            2: _circle_active(controller_states, 2),
        }

        for slot_id in (1, 2):
            if circle_state[slot_id] != previous_circle[slot_id]:
                last_change_time[slot_id] = now

        if last_change_time[1] is not None and last_change_time[2] is not None:
            comparison_count += 1
            delta = last_change_time[1] - last_change_time[2]
            delta_ms = abs(delta) * 1000

            if abs(delta) <= simultaneous_threshold:
                print(
                    f"circle_race comparison {comparison_count}: slots 1 and 2 updated simultaneously (Δt≈{delta_ms:.3f} ms)"
                )
            elif delta < 0:
                print(
                    f"circle_race comparison {comparison_count}: slot 1 updated {delta_ms:.3f} ms before slot 2"
                )
            else:
                print(
                    f"circle_race comparison {comparison_count}: slot 2 updated {delta_ms:.3f} ms before slot 1"
                )

            last_change_time = {1: None, 2: None}

        previous_circle = circle_state

        time.sleep(frame_delay / 2)
