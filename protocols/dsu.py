# DSU protocol handling utilities

from __future__ import annotations

import struct
import time
import socket
import threading

from typing import Iterable

from ..libraries import packet
from ..libraries import net_config as net_cfg
from ..libraries.masks import ControllerStateDict


class DSUProtocol:
    """Handle DSU network traffic for the server."""

    def __init__(self, server_id: int | None = None) -> None:
        self.server_id = server_id
        self._prev_connection_types: dict[int, int] = {}
        self._idle_slots: set[int] = set()

    def initialize(
        self,
        sock: socket.socket,
        controller_states: ControllerStateDict,
        stop_event: threading.Event,
        idle_slots: Iterable[int] | None = None,
    ) -> None:
        """Prepare the protocol state for a running server."""
        packet.start_sender(sock, stop_event)
        packet.controller_states = controller_states
        if self.server_id is not None:
            packet.server_id = self.server_id
            net_cfg.server_id = self.server_id
        if idle_slots is not None:
            self._idle_slots = set(idle_slots)
        else:
            self._idle_slots = set()

    def handle_requests(self, sock: socket.socket) -> None:
        """Process any pending DSU requests from ``sock``."""
        try:
            while True:
                data, addr = sock.recvfrom(2048)
                if data[:4] != b"DSUC":
                    continue
                if len(data) < 20:
                    continue
                msg_type, = struct.unpack("<I", data[16:20])
                if msg_type == net_cfg.DSU_version_request:
                    packet.handle_version_request(addr)
                elif msg_type == net_cfg.DSU_list_ports:
                    packet.handle_list_ports(addr, data)
                elif msg_type == net_cfg.DSU_button_request:
                    packet.handle_pad_data_request(addr, data)
                elif msg_type == net_cfg.DSU_motor_request:
                    packet.handle_motor_request(addr, data)
                elif msg_type == net_cfg.motor_command:
                    packet.handle_motor_command(addr, data)
        except BlockingIOError:
            pass
        except ConnectionResetError as exc:
            print(f"Client connection reset: {exc}")
            for client in list(net_cfg.active_clients):
                print(f"Removing client {client} due to connection reset")
            net_cfg.active_clients.clear()
        except Exception as exc:
            print(f"Error processing packet: {exc}")

    def update_clients(self, controller_states: ControllerStateDict) -> None:
        """Send controller state updates to connected clients."""
        now = time.time()
        for addr in list(net_cfg.active_clients.keys()):
            if now - net_cfg.active_clients[addr]["last_seen"] > net_cfg.DSU_timeout:
                del net_cfg.active_clients[addr]
                print(f"Client {addr} timed out")

        for s, state in list(controller_states.items()):
            prev_connected = state.connected
            prev_type = self._prev_connection_types.get(s, state.connection_type)
            if s in self._idle_slots:
                state.connected = True
            else:
                state.update_connection(net_cfg.stick_deadzone)

            if state.connection_type != prev_type:
                self._prev_connection_types[s] = state.connection_type
                if state.connection_type == -1:
                    state.connected = False
                    net_cfg.known_slots.discard(s)
                    for client in list(net_cfg.active_clients):
                        packet.send_port_disconnect(client, s)
                else:
                    net_cfg.known_slots.add(s)
                    for client in list(net_cfg.active_clients):
                        packet.send_port_info(client, s)

            if (
                state.connection_type != -1
                and not prev_connected
                and state.connected
                and s not in net_cfg.known_slots
            ):
                net_cfg.known_slots.add(s)
                for client in list(net_cfg.active_clients):
                    packet.send_port_info(client, s)
            if state.connection_type != -1:
                for addr in list(net_cfg.active_clients):
                    packet.send_input(
                        addr,
                        s,
                        connected=state.connected,
                        packet_num=state.packet_num,
                        buttons1=state.buttons1,
                        buttons2=state.buttons2,
                        home=state.home,
                        touch_button=state.touch_button,
                        L_stick=state.L_stick,
                        R_stick=state.R_stick,
                        dpad_analog=state.dpad_analog,
                        face_analog=state.face_analog,
                        analog_R1=state.analog_R1,
                        analog_L1=state.analog_L1,
                        analog_R2=state.analog_R2,
                        analog_L2=state.analog_L2,
                        touchpad_input1=state.touchpad_input1,
                        touchpad_input2=state.touchpad_input2,
                        motion_timestamp=state.motion_timestamp,
                        accelerometer=state.accelerometer,
                        gyroscope=state.gyroscope,
                        connection_type=state.connection_type,
                        battery=state.battery,
                    )
        for state in list(controller_states.values()):
            state.packet_num = (state.packet_num + 1) & 0xFFFFFFFF
            motors = list(state.motors)
            timestamps = list(state.motor_timestamps)
            for i in range(state.motor_count):
                if now - timestamps[i] > net_cfg.DSU_timeout and motors[i] != 0:
                    motors[i] = 0
            state.motors = tuple(motors)

    def shutdown(self) -> None:
        """Clean up protocol specific state."""
        packet.stop_sender()
