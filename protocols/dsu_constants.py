"""Constants for the DSU protocol."""

# DSU Message Types
DSU_version_request  = 0x100000
DSU_version_response = 0x100000
DSU_list_ports       = 0x100001
DSU_port_info        = 0x100001
DSU_button_request   = 0x100002
DSU_button_response  = 0x100002
DSU_motor_request    = 0x110001
DSU_motor_response   = 0x110001
motor_command        = 0x110002

# Protocol version used when communicating with clients
PROTOCOL_VERSION = 1001
