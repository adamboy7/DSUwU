"""Synthetic input pulses for benchmarking two DSU slots.

Configure the slots and buttons with the following optional environment
variables before launching the DSUwU server:

- ``BENCHMARK_SLOT_A``: Primary slot index (default: ``0``)
- ``BENCHMARK_SLOT_B``: Secondary slot index (default: ``1``)
- ``BENCHMARK_BUTTON_A``: Button name toggled on slot A (default: ``cross``)
- ``BENCHMARK_BUTTON_B``: Button name toggled on slot B (default: ``circle``)
- ``BENCHMARK_PULSE_FRAMES``: How many 1/60th second frames to keep the
  buttons pressed (default: ``1``)
- ``BENCHMARK_PULSE_INTERVAL``: Seconds to wait between pulses (default:
  ``frame_delay * 2``)

Only the lowest configured slot runs the loop so the script can be safely
assigned to both slots without double‑toggling the inputs.
"""

from __future__ import annotations

import os
import time

from libraries import net_config as net_cfg
from libraries.inputs import VALID_BUTTONS, frame_delay, pulse_button_xor


def _get_int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


def _get_float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


def _button(name: str, fallback: str) -> str:
    value = os.environ.get(name, fallback).strip().lower()
    if value not in VALID_BUTTONS:
        raise ValueError(
            f"invalid button '{value}' for {name}; choose from: {', '.join(sorted(VALID_BUTTONS))}"
        )
    return value


def controller_loop(stop_event, controller_states, slot):
    slot_a = _get_int("BENCHMARK_SLOT_A", 0)
    slot_b = _get_int("BENCHMARK_SLOT_B", 1)
    button_a = _button("BENCHMARK_BUTTON_A", "cross")
    button_b = _button("BENCHMARK_BUTTON_B", "circle")
    pulse_frames = max(1, _get_int("BENCHMARK_PULSE_FRAMES", 1))
    interval = max(frame_delay, _get_float("BENCHMARK_PULSE_INTERVAL", frame_delay * 2))

    # Ensure both configured slots exist so the benchmark can target any indices.
    net_cfg.ensure_slot(max(slot_a, slot_b))
    controller_states[slot_a].connected = True
    controller_states[slot_b].connected = True

    # Only the lowest configured slot performs the pulses to avoid duplicate loops
    # if the script is assigned to both slots.
    if slot not in (slot_a, slot_b) or slot != min(slot_a, slot_b):
        stop_event.wait()
        return

    print(
        f"Benchmarking slots {slot_a} ({button_a}) and {slot_b} ({button_b}) "
        f"with {pulse_frames} frame pulses every {interval:.3f}s"
    )

    while not stop_event.is_set():
        pulse_button_xor(pulse_frames, controller_states, slot_a, button_a)
        if slot_b != slot_a:
            pulse_button_xor(pulse_frames, controller_states, slot_b, button_b)
        time.sleep(interval)
