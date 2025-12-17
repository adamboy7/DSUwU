import queue
import struct
import unittest

from libraries import net_config as net_cfg
from protocols import dsu_packet
from protocols.dsu_constants import DSU_version_response, PROTOCOL_VERSION


class HandleVersionRequestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_send_queue = dsu_packet.send_queue
        self.original_sock = dsu_packet.sock
        self.original_active_clients = dict(net_cfg.active_clients)

        dsu_packet.send_queue = queue.Queue()
        net_cfg.active_clients.clear()

    def tearDown(self) -> None:
        dsu_packet.send_queue = self.original_send_queue
        dsu_packet.sock = self.original_sock

        net_cfg.active_clients.clear()
        net_cfg.active_clients.update(self.original_active_clients)

    def test_version_response_uses_full_payload_and_protocol_version_header(self) -> None:
        addr = ("127.0.0.1", 26760)

        dsu_packet.handle_version_request(addr, 1)

        pkt, queued_addr, desc = dsu_packet.send_queue.get_nowait()
        self.assertEqual(queued_addr, addr)
        self.assertEqual(desc, "version response")

        header = pkt[:16]
        msg = pkt[16:]

        sig, header_protocol_version, msg_len, crc_value, _server_id = struct.unpack(
            "<4sHHII", header
        )
        self.assertEqual(sig, b"DSUS")
        self.assertEqual(header_protocol_version, PROTOCOL_VERSION)
        self.assertEqual(msg_len, 8)
        self.assertEqual(len(msg), 8)

        msg_type, version, reserved = struct.unpack("<IHH", msg)
        self.assertEqual(msg_type, DSU_version_response)
        self.assertEqual(version, PROTOCOL_VERSION)
        self.assertEqual(reserved, 0)

        self.assertEqual(crc_value, dsu_packet.crc_packet(header, msg))


if __name__ == "__main__":
    unittest.main()
