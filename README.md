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
example controller scripts found in `demo/` to generate input. Custom scripts can
be supplied per slot with the `--controllerN-script` arguments. Slots beyond 4
are non‑standard but can be enabled by providing `--controller5-script`,
`--controller6-script`, and so on. When extra scripts are supplied the server
will create that many controller slots.

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

