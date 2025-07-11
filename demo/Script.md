## How do I create my own controller script?
It is recommended you import `time`, `libraries.inputs`, and `libraries.masks`, but the bare minimum is to wrap your code into a function named `controller_loop(stop_event, controller_states, slot)`

**stop_event** is a value passed to the function to allow the server to run your function in its own thread, and should be used to gracefully terminate your script. `while not stop_event.is_set():`

**controller_states** is the dictionary of connected controllers maintained by the server, and contains relevant controller inputs, motion, touch, and vibration. Contains information for every controller slot, not just your own.

**slot** is the slot number your thread is running as, assigned by the server. Allows you to locate and use the appropriate `controller_states[slot]` entry, while still allowing cross slot access.

## How do I do things?

**Push a button:** `pulse_button(frame, controller_states, slot, **button_kwargs)`, **frame** being how many 1/60ths of a second you want to press the button, **controller_states** being the server's controller dictionary, **slot** being the controller slot number you want to update, and **button_kwargs**. Defaults back to unpressed after finishing.
Overwrites the entire controller state to match the exact buttons you describe. If you just say `circle=True` and nothing else, it will unpress everything else.
Both `pulse_button` and `pulse_button_xor` accept button names from either mask
(``share``/``l3``/``r3``/``options``/``up``/``right``/``down``/``left`` and
``l2``/``r2``/``l1``/``r1``/``triangle``/``circle``/``cross``/``square``) as
well as ``home``.

**Toggle a button:** `pulse_button_xor(frame, controller_states, slot, *buttons)`, **frame** being how many 1/60ths of a second you want to press the button, **controller_states** being the server's controller dictionary, **slot** being the controller slot number you want to update, and ``*buttons`` representing one or more button names (e.g. ``"circle"``).
Allows you to selectively write button states without modifying other buttons that share a "button mask". Useful if multiple functions or threads are sharing a slot. Keyword arguments are still accepted but ``False`` values are ignored.

**Replay a captured input log:** `Replay_Inputs(Inputs_Path, Motion_Path)`,
**Inputs_Path** being the file path to an input capture, **Motion_Path** being the file path to a motion capture (likely generated by DSOwO). Motion_Path can be left as `None` if your script doesn't rely on motion data.

**Set your controller's mac address:** `set_slot_mac_address(slot, mac)`,
**slot** being the controller slot number you want to update, followed by a `b"\xAA\xBB\xCC\xDD\xEE"` format **mac** string. Some clients rely on mac address registration. If one is not defined, it defaults to being based off your slot number.

**Set your controller's connection type:** `set_slot_connection_type(controller_states, slot, conn_type)`,
**controller_states** being the server's controller dictionary, **slot** being the controller slot number you want to update, and **conn_type** relevant values are `0` for N/A, `1` for USB, `2` for bluetooth, and `-1` is a special scripted flag that sends a port disconnect to the client, updates the port info appropriately, and deletes the controller dictionary entry from the server.
