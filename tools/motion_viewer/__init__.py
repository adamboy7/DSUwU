"""3D motion controller viewer tool.

Opens a Toplevel window showing a 3D game-controller model that rotates in
real-time to match the physical controller's orientation, driven by the DSU
accelerometer and gyroscope data.
"""

import math
import tkinter as tk
from tkinter import ttk

__all__ = ["MotionControllerViewer"]

# ---------------------------------------------------------------------------
# Controller 3D geometry
# ---------------------------------------------------------------------------

# Vertices: (x, y, z) in right-hand coords (x=right, y=up, z=toward player).
# The controller rests flat (face up) at rest.
_VERTICES = [
    # Main body – rectangular box
    #   bottom face
    (-1.0, -0.175, -0.7),   # 0
    ( 1.0, -0.175, -0.7),   # 1
    ( 1.0, -0.175,  0.7),   # 2
    (-1.0, -0.175,  0.7),   # 3
    #   top face
    (-1.0,  0.175, -0.7),   # 4
    ( 1.0,  0.175, -0.7),   # 5
    ( 1.0,  0.175,  0.7),   # 6
    (-1.0,  0.175,  0.7),   # 7

    # Left grip – box hanging below-left
    (-1.0, -0.175,  0.1),   # 8
    (-0.4, -0.175,  0.1),   # 9
    (-0.4, -0.175,  0.7),   # 10
    (-1.0, -0.175,  0.7),   # 11
    (-1.0, -0.75,   0.1),   # 12
    (-0.4, -0.75,   0.1),   # 13
    (-0.4, -0.75,   0.7),   # 14
    (-1.0, -0.75,   0.7),   # 15

    # Right grip – mirror of left
    ( 0.4, -0.175,  0.1),   # 16
    ( 1.0, -0.175,  0.1),   # 17
    ( 1.0, -0.175,  0.7),   # 18
    ( 0.4, -0.175,  0.7),   # 19
    ( 0.4, -0.75,   0.1),   # 20
    ( 1.0, -0.75,   0.1),   # 21
    ( 1.0, -0.75,   0.7),   # 22
    ( 0.4, -0.75,   0.7),   # 23
]

# Faces: each entry is a dict with vertex indices, fill colour, outline colour.
# Draw order is determined at render time (painter's algorithm).
_FACES = [
    # --- Main body ---
    # top  (lightest – faces the player when flat on table)
    {"verts": [4, 5, 6, 7],    "color": "#3a3a5c", "outline": "#555580"},
    # bottom
    {"verts": [3, 2, 1, 0],    "color": "#1a1a2e", "outline": "#111133"},
    # front (near player, positive-z side)
    {"verts": [3, 7, 6, 2],    "color": "#2a2a45", "outline": "#444466"},
    # back
    {"verts": [0, 1, 5, 4],    "color": "#222240", "outline": "#333360"},
    # left side
    {"verts": [0, 4, 7, 3],    "color": "#25253e", "outline": "#3a3a58"},
    # right side
    {"verts": [1, 2, 6, 5],    "color": "#25253e", "outline": "#3a3a58"},

    # --- Left grip ---
    {"verts": [8,  9,  13, 12], "color": "#2a2a45", "outline": "#444466"},  # front
    {"verts": [11, 15, 14, 10], "color": "#2a2a45", "outline": "#444466"},  # back
    {"verts": [8,  11, 15, 12], "color": "#25253e", "outline": "#3a3a58"},  # left
    {"verts": [9,  10, 14, 13], "color": "#25253e", "outline": "#3a3a58"},  # right
    {"verts": [12, 13, 14, 15], "color": "#1a1a2e", "outline": "#111133"},  # bottom

    # --- Right grip ---
    {"verts": [16, 17, 21, 20], "color": "#2a2a45", "outline": "#444466"},  # front
    {"verts": [19, 23, 22, 18], "color": "#2a2a45", "outline": "#444466"},  # back
    {"verts": [17, 18, 22, 21], "color": "#25253e", "outline": "#3a3a58"},  # right
    {"verts": [16, 20, 23, 19], "color": "#25253e", "outline": "#3a3a58"},  # left
    {"verts": [20, 21, 22, 23], "color": "#1a1a2e", "outline": "#111133"},  # bottom

    # --- Top-face decorations (flat quads sitting on y=0.176) ---
    # Left analog stick (small dark disc approximated as a square)
    {"verts": None, "circle": (-0.50, 0.176, -0.10), "r": 0.20,
     "color": "#1a1a1a", "outline": "#555555"},
    # Right analog stick
    {"verts": None, "circle": ( 0.30, 0.176,  0.20), "r": 0.20,
     "color": "#1a1a1a", "outline": "#555555"},
    # Face-buttons cluster
    {"verts": None, "circle": ( 0.65, 0.176, -0.10), "r": 0.18,
     "color": "#2d2d2d", "outline": "#666666"},
    # D-pad area
    {"verts": None, "circle": (-0.65, 0.176,  0.20), "r": 0.18,
     "color": "#2d2d2d", "outline": "#666666"},
]

# ---------------------------------------------------------------------------
# Math helpers (pure Python – no numpy)
# ---------------------------------------------------------------------------

def _matmul(A, B):
    """3×3 matrix multiply."""
    n = 3
    return [
        [sum(A[r][k] * B[k][c] for k in range(n)) for c in range(n)]
        for r in range(n)
    ]


def _matvec(M, v):
    """3×3 matrix × 3-vector."""
    return [sum(M[r][c] * v[c] for c in range(3)) for r in range(3)]


def _rotation_x(angle):
    """Rotation matrix around X axis."""
    c, s = math.cos(angle), math.sin(angle)
    return [[1, 0,  0],
            [0, c, -s],
            [0, s,  c]]


def _rotation_y(angle):
    """Rotation matrix around Y axis."""
    c, s = math.cos(angle), math.sin(angle)
    return [[ c, 0, s],
            [ 0, 1, 0],
            [-s, 0, c]]


def _rotation_z(angle):
    """Rotation matrix around Z axis."""
    c, s = math.cos(angle), math.sin(angle)
    return [[c, -s, 0],
            [s,  c, 0],
            [0,  0, 1]]


# ---------------------------------------------------------------------------
# Main viewer class
# ---------------------------------------------------------------------------

class MotionControllerViewer:
    """Toplevel window: 3D controller that follows real controller orientation."""

    CANVAS_W = 500
    CANVAS_H = 420
    SCALE    = 130          # world-unit → pixel scale
    FOV      = 400          # perspective focal length in pixels
    Z_OFFSET = 3.5          # push model away from camera so z denominator > 0
    UPDATE_MS = 33          # ~30 fps

    # Complementary filter weights
    ALPHA    = 0.95         # gyro weight (higher → smoother, slower drift correction)
    DEG_TO_RAD = math.pi / 180.0

    def __init__(self, parent, client):
        self.client = client
        self._pitch = 0.0   # radians – tilt toward / away from player
        self._roll  = 0.0   # radians – tilt left / right
        self._yaw   = 0.0   # radians – twist (gyro-integrated only)
        self._last_ts: int | None = None
        self._after_job = None

        self.window = tk.Toplevel(parent)
        self.window.title("Motion Controller Viewer")
        self.window.resizable(False, False)
        self.window.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_ui()
        self._refresh_slots()
        self._schedule_update()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        top = ttk.Frame(self.window, padding=4)
        top.pack(fill="x")

        ttk.Label(top, text="Slot:").pack(side="left")
        self._slot_var = tk.StringVar()
        self._slot_combo = ttk.Combobox(top, textvariable=self._slot_var,
                                        width=6, state="readonly")
        self._slot_combo.pack(side="left", padx=(2, 12))

        ttk.Button(top, text="Reset orientation",
                   command=self._reset_orientation).pack(side="left")

        self._canvas = tk.Canvas(self.window,
                                 width=self.CANVAS_W, height=self.CANVAS_H,
                                 bg="#0d0d1a", highlightthickness=0)
        self._canvas.pack()

        self._status = ttk.Label(self.window, text="", font=("Courier", 9))
        self._status.pack(pady=2)

    def _refresh_slots(self):
        slots = sorted(self.client.states.keys())
        values = [str(s) for s in slots]
        self._slot_combo["values"] = values
        if values and self._slot_var.get() not in values:
            self._slot_var.set(values[0])

    def _reset_orientation(self):
        self._pitch = 0.0
        self._roll  = 0.0
        self._yaw   = 0.0
        self._last_ts = None

    # ------------------------------------------------------------------
    # Orientation tracking
    # ------------------------------------------------------------------

    def _update_orientation(self, accel, gyro, motion_ts):
        """Apply complementary filter to update pitch/roll/yaw."""
        ax, ay, az = accel
        gx, gy, gz = gyro

        # Accelerometer-derived absolute tilt (Y is up axis; flat → ay≈1, ax≈az≈0)
        norm = math.sqrt(ax*ax + ay*ay + az*az)
        if norm > 0.01:
            accel_pitch = math.atan2(-az, ay)   # rotation around X: 0 when flat
            accel_roll  = math.atan2(ax, ay)    # rotation around Z: 0 when flat
        else:
            accel_pitch = self._pitch
            accel_roll  = self._roll

        # Integrate gyroscope for dt
        if self._last_ts is not None and motion_ts != self._last_ts:
            # motion_timestamp is in microseconds
            dt = (motion_ts - self._last_ts) / 1_000_000.0
            dt = max(0.0, min(dt, 0.5))  # clamp to sane range
        else:
            dt = self.UPDATE_MS / 1000.0
        self._last_ts = motion_ts

        # DS4/DualSense: gx=pitch rate, gy=yaw rate, gz=roll rate (all in deg/s)
        gyro_pitch = self._pitch + gx * self.DEG_TO_RAD * dt
        gyro_roll  = self._roll  + gz * self.DEG_TO_RAD * dt
        self._yaw  += gy * self.DEG_TO_RAD * dt

        # Complementary filter: blend gyro integration with accel correction
        self._pitch = self.ALPHA * gyro_pitch + (1 - self.ALPHA) * accel_pitch
        self._roll  = self.ALPHA * gyro_roll  + (1 - self.ALPHA) * accel_roll

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _build_rotation_matrix(self):
        """Combined rotation matrix for current pitch / roll / yaw."""
        Rx = _rotation_x(self._pitch)
        Rz = _rotation_z(self._roll)
        Ry = _rotation_y(self._yaw)
        return _matmul(Ry, _matmul(Rx, Rz))

    def _project(self, v, R):
        """Rotate vertex, then apply perspective projection → (sx, sy, depth)."""
        rx, ry, rz = _matvec(R, v)
        z = rz + self.Z_OFFSET
        if z < 0.01:
            z = 0.01
        scale = self.FOV / (self.FOV + z)
        cx = self.CANVAS_W / 2
        cy = self.CANVAS_H / 2
        sx = cx + rx * scale * self.SCALE
        sy = cy - ry * scale * self.SCALE   # y-up in 3D → y-down on screen
        return sx, sy, rz

    def _circle_polygon(self, cx, cy, cz, r, R, n=12):
        """Return (2d_coords, avg_depth) for a circle approximated as n-gon."""
        pts_2d = []
        depths = []
        for i in range(n):
            angle = 2 * math.pi * i / n
            vx = cx + r * math.cos(angle)
            vz = cz + r * math.sin(angle)
            sx, sy, depth = self._project([vx, cy, vz], R)
            pts_2d.extend([sx, sy])
            depths.append(depth)
        return pts_2d, sum(depths) / len(depths)

    def _draw(self, R):
        """Clear canvas and repaint all faces sorted back-to-front."""
        self._canvas.delete("all")

        verts_3d = [list(v) for v in _VERTICES]

        # Pre-project all quad vertices
        projected = [self._project(v, R) for v in verts_3d]

        render_list = []

        for face in _FACES:
            if face.get("circle") is not None:
                # Circular decoration on the top face
                cx, cy, cz = face["circle"]
                coords, avg_z = self._circle_polygon(cx, cy, cz, face["r"], R)
                render_list.append((avg_z, coords, face["color"], face["outline"]))
            else:
                idxs = face["verts"]
                pts  = [projected[i] for i in idxs]
                avg_z = sum(p[2] for p in pts) / len(pts)
                coords = []
                for sx, sy, _ in pts:
                    coords.extend([sx, sy])
                render_list.append((avg_z, coords, face["color"], face["outline"]))

        # Painter's algorithm: draw farthest faces first
        render_list.sort(key=lambda x: x[0])

        for _, coords, color, outline in render_list:
            if len(coords) >= 6:
                self._canvas.create_polygon(
                    coords, fill=color, outline=outline, width=1
                )

        # Draw a subtle axis indicator (small lines from centre)
        self._draw_axes(R)

    def _draw_axes(self, R):
        """Draw XYZ axis lines in the lower-right corner for reference."""
        ox, oy = self.CANVAS_W - 55, self.CANVAS_H - 45
        length = 30
        axes = [
            ([1, 0, 0], "#e05555", "X"),
            ([0, 1, 0], "#55e055", "Y"),
            ([0, 0, 1], "#5555e0", "Z"),
        ]
        for direction, color, label in axes:
            rx, ry, rz = _matvec(R, direction)
            z = rz + self.Z_OFFSET
            if z < 0.01:
                z = 0.01
            scale = self.FOV / (self.FOV + z)
            ex = ox + rx * scale * length
            ey = oy - ry * scale * length
            self._canvas.create_line(ox, oy, ex, ey, fill=color, width=2)
            self._canvas.create_text(ex, ey, text=label, fill=color,
                                     font=("Courier", 8, "bold"))

    # ------------------------------------------------------------------
    # Update loop
    # ------------------------------------------------------------------

    def _schedule_update(self):
        self._after_job = self.window.after(self.UPDATE_MS, self._update)

    def _update(self):
        self._refresh_slots()

        slot_str = self._slot_var.get()
        try:
            slot = int(slot_str)
        except (ValueError, TypeError):
            slot = None

        state = self.client.states.get(slot) if slot is not None else None

        if state:
            accel = state.get("accel", (0.0, 0.0, 1.0))
            gyro  = state.get("gyro",  (0.0, 0.0, 0.0))
            ts    = state.get("motion_ts", 0)
            self._update_orientation(accel, gyro, ts)

            ax, ay, az = accel
            gx, gy, gz = gyro
            self._status.config(
                text=(f"accel ({ax:+.2f}, {ay:+.2f}, {az:+.2f})  "
                      f"gyro ({gx:+.2f}, {gy:+.2f}, {gz:+.2f})")
            )
        else:
            self._status.config(text="No data")

        R = self._build_rotation_matrix()
        self._draw(R)

        self._schedule_update()

    def _on_close(self):
        if self._after_job is not None:
            self.window.after_cancel(self._after_job)
            self._after_job = None
        self.window.destroy()
