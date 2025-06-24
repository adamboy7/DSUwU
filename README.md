# DSUwU

DSUwU is a small Python implementation of the DSU protocol. It provides a
minimal server that broadcasts controller state and a simple viewer for
inspecting DSU packets. The project is useful for testing or debugging
applications that consume DSU controller input.

## Prerequisites

- Python 3.10 or newer (for `|` type union syntax)
- Tkinter for the viewer (usually included with standard Python installs)

No third‑party packages are required.

## Running the server

```
python server.py [--port PORT] [--server-id HEX] [--controller1-script PATH]
                 [--controller2-script PATH] [--controller3-script PATH]
                 [--controller4-script PATH] [--controller5-script PATH ...]
```

If no options are provided the server listens on UDP port 26760 and uses the
example controller scripts found in `demo/` to generate input. These include
`circle_loop.py`, `cross_loop.py`, `square_loop.py`, `triangle_loop.py`, and an
`idle_loop.py` that keeps a slot connected without sending input. Custom scripts
can be supplied per slot with the `--controllerN-script` arguments. A
`pygame_controller.py` script is also provided for capturing real controller
input using the `pygame` library. Set ``JOYSTICK_INDEX`` near the top of that
file to choose which joystick to read when multiple are connected. Slots beyond 4
are non‑standard but can be enabled by providing `--controller5-script`,
`--controller6-script`, and so on. When extra scripts are supplied the server
will create that many controller slots. Use `None` to omit the controller
thread for a slot while still allocating it.

Slots without a script start disconnected. To keep such a slot connected as an
idle buffer, set `controller_states[slot].idle = True` after calling
`start_server()`. Accessing a non-existent slot will automatically create it so
no extra setup is required.
Passing `None` as the script path (any case) initializes a slot without running
a controller loop.

## Running the viewer

The viewer connects to a DSU server and displays the state of up to four
controllers.

```
python viewer.py
```

By default it connects to `127.0.0.1` on port `26760`. The port can be changed
from the **Options → Port** menu once the GUI is running. Use **Options →
Remote Connection** to connect to a different DSU server without restarting the
program.

The viewer also includes a **Rebroadcast** tool found under **Tools →
Rebroadcast**. This feature launches a temporary DSU server that mirrors the
data captured by the viewer. When prompted for the rebroadcast port (default is
`26761`), enter the desired port and the viewer will forward all input data to
that port so other applications can consume it.

The **Start input capture** tool lets you record controller input to a file.
Choose the save location when prompted and the viewer will log state changes as
JSON lines. Only button masks, sticks, triggers and touch values are recorded,
so motion data is ignored. While capturing, the menu entry changes to **Stop
input capture** which ends the capture and reverts the menu.

The **Start motion capture** tool records accelerometer and gyroscope values at
a high polling rate. After selecting the save location, the viewer writes a
JSON line for each poll including the timestamp, slot, motion timestamp,
accelerometer and gyro readings. While active, the menu entry shows **Stop
motion capture** to end the session.

The **Packet Parser** tool opens a window with a scrolling text box where raw
DSU packet bytes can be pasted (for example from a Wireshark capture). After
pressing **Parse** you can step through the packets using **Next** and
**Prev** to inspect each message type, including decoded button states for
input responses.

