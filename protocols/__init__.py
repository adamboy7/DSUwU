"""Protocol implementations for DSUwU.

This module intentionally avoids importing submodules eagerly to prevent
import cycles when modules such as :mod:`libraries.net_config` load protocol
constants.
"""

_exports = {
    "DSUProtocol": "dsu",
    "DSU_version_request": "dsu_constants",
    "DSU_version_response": "dsu_constants",
    "DSU_list_ports": "dsu_constants",
    "DSU_port_info": "dsu_constants",
    "DSU_button_request": "dsu_constants",
    "DSU_button_response": "dsu_constants",
    "DSU_motor_request": "dsu_constants",
    "DSU_motor_response": "dsu_constants",
    "motor_command": "dsu_constants",
    "PROTOCOL_VERSION": "dsu_constants",
}

__all__ = list(_exports)


def __getattr__(name):
    if name not in _exports:
        raise AttributeError(name)
    module_name = _exports[name]
    module = __import__(f"{__name__}.{module_name}", fromlist=[name])
    return getattr(module, name)
