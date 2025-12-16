import struct

from viewer import DSU_button_response, parse_button_response


def test_truncated_payload_is_ignored():
    # Build a packet that passes the initial header and button checks but
    # truncates before the touchpad data, which previously triggered an
    # unpack error.
    payload_length = 41  # Enough for header + buttons (36) but shorter than touchpad start (42).
    data = bytearray(20 + payload_length)
    struct.pack_into("<I", data, 16, DSU_button_response)

    assert parse_button_response(bytes(data)) is None
