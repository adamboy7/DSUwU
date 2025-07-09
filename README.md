# DSUwU

DSUwU is a Python implementation of the DSU protocol. It provides a server that tracks and simulates inputs for Cemu and other "cemuhook" like clients. A simple viewer for
inspecting DSU packets is also included in DSOwO. The project is useful for scripting automated controller actions, allowing you to turn functionally anything into a controller.

## Prerequisites

- Python 3.10 or newer (for `|` type union syntax)
- Tkinter for the viewer (usually included with standard Python installs)

No third‑party packages are required.

## Running the server

```
python server.py [--port PORT] [--server-id HEX]
                 [--controller1-script PATH] [--controller2-script PATH]
                 [--controller3-script PATH] [--controller4-script PATH]
```

If no options are provided the server listens on UDP port 26760 and uses the
example controller scripts found in `demo/` to generate input. Custom scripts
can be supplied per slot with the `--controllerN-script` arguments. Slot 0 is
disabled by default but can be manually enabled with `--controller0-script`,
which starts disconnected unless a script is specified. A
`demo/pygame_controller.py` script is also provided for capturing real controller
input using the `pygame` library, if for some reason you don't want to use DS4Windows ¯\_(ツ)_/¯

Slots beyond four are non‑standard but can be enabled by providing
`--controller5-script`, `--controller6-script`, and so on, up to a soft limit of
256. (Slots beyond 256 can still technically be created, but standard port info
packet structures have a 1 byte limit. Most standard clients "tolerate" 8 controllers).
Passing `None` as the script path (any case) keeps the slot disconnected, without
creating any additional threads. Using `idle` instead (any case) marks the slot as connected and initializes a controller object, without
creating any additional threads. Scripts can read and write to other slots (at a small risk of input race conditions), accessing a non-existent slot will automatically create it.

Note that some clients may assume a slot 0 is the first slot, in which case a visual "off by one" quirk can happen. (I know, I'm probably doing it wrong. I'm a wierdo who wants slot 0 both availible and disconnected :P)

## Running the viewer

DSOwO connects to a DSU server and displays the state of connected
controllers. It automatically detects additional slots. When five or
more controllers are present, the UI switches from a tabbed layout to a
drop-down selector. Other tools are bundled in for use in debugging.

```
python viewer.py
```

By default it connects to `127.0.0.1` on port `26760`. The port can be changed
from the **Options → Port** menu once the GUI is running. Use **Options →
Remote Connection** to connect to a remote DSU server, rather than the typical localhost.

**Tools → Rebroadcast**. This tool launches a sub-DSU server that mirrors the
data captured by the viewer. When prompted for the rebroadcast port (default is
`26761`), enter the desired port and the viewer will forward all input data
so other applications can consume it. (DSU protocol supports multiple clients, this feature was mostly just for me :P)

**Tools → Start input capture**. This tool lets you record controller input to a file.
Choose the save location when prompted and the viewer will log state changes as
JSON lines. Motion data is ignored for the sake of reasonable log sizes. While capturing, the menu entry changes to **Stop
input capture** which ends the capture and reverts the menu. Mainly for use in the `Replay_Inputs(*Inputs_Path*, Motion_Path)` script function.

**Tools → Start motion capture**. This tool records accelerometer and gyroscope values
whenever the viewer receives a new packet. Choose the save location when prompted, the
viewer writes a JSON line each time the motion data changes containing the
timestamp, slot, motion timestamp, accelerometer and gyro readings. While
active, the menu entry shows **Stop motion capture** to end the session. Mainly for use in the `Replay_Inputs(Inputs_Path, *Motion_Path*)` script function.

**Tools → Packet Parser**. This tool opens a window with a scrolling text box where raw
DSU packet bytes can be pasted (for example from a Wireshark capture). After
pressing **Parse** you can step through the packets using **Next** and
**Prev** to inspect each message type, including decoded button states for
input responses. (Helps answer the age old question, "WHY ISN'T IT WORKING?")

## What the heck is a DSU?

DSU is the standard based on the old Cemuhook plugin for motion data. Mainly implemented and supported by DS4Windows. It lets you send button, joystick, trigger, analog button presses, motion, touch, and "unofficially" vibration. A cross between a PS3/4 style controller and a WiiU gamepad, runs on UDP networking.

https://v1993.github.io/cemuhook-protocol/
