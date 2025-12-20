import struct

import pytest

from libraries import net_config as net_cfg
from protocols import dsu_packet
from protocols.dsu_constants import DSU_version_response, PROTOCOL_VERSION


def test_handle_version_request_advertises_negotiated_version(monkeypatch):
    addr = ("127.0.0.1", 12345)
    captured: dict[str, object] = {}

    def fake_queue_packet(pkt, dest, desc=None):
        captured["pkt"] = pkt
        captured["addr"] = dest
        captured["desc"] = desc

    monkeypatch.setattr(dsu_packet, "queue_packet", fake_queue_packet)

    negotiated_version = PROTOCOL_VERSION - 1
    net_cfg.active_clients.clear()

    dsu_packet.handle_version_request(addr, negotiated_version)

    assert captured["addr"] == addr
    pkt = captured["pkt"]
    header = pkt[:16]
    msg = pkt[16:]

    signature, version, *_ = struct.unpack("<4sHHII", header)
    assert signature == b"DSUS"
    assert version == negotiated_version

    msg_type, payload_version, reserved = struct.unpack("<IHH", msg[:8])
    assert msg_type == DSU_version_response
    assert payload_version == negotiated_version
    assert reserved == 0
