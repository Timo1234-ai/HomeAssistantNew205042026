"""TP-Link Kasa plugin (community).

Communicates with TP-Link Kasa smart plugs and bulbs using their
local encrypted UDP/TCP API.
"""

from __future__ import annotations

import json
import socket
import struct
from typing import Any

# Lazy import of plugin base
try:
    from home_assistant.devices.plugin_manager import DevicePlugin
except ImportError:
    class DevicePlugin:  # type: ignore[no-redef]
        def __init__(self, device_ip: str, **kwargs: Any) -> None:
            self.device_ip = device_ip
            self.config = kwargs
        def get_state(self) -> dict: return {}
        def execute(self, cmd: str, p: dict) -> dict: return {}
        def get_capabilities(self) -> list: return []


def _encrypt(payload: str) -> bytes:
    key = 171
    result = struct.pack(">I", len(payload))
    for char in payload:
        enc = key ^ ord(char)
        key = enc
        result += bytes([enc])
    return result


def _decrypt(data: bytes) -> str:
    key = 171
    result = ""
    for byte in data[4:]:
        dec = key ^ byte
        key = byte
        result += chr(dec)
    return result


class TplinkKasaPlugin(DevicePlugin):
    """Control TP-Link Kasa smart plugs and bulbs."""

    name = "TP-Link Kasa"
    plugin_id = "tplink_kasa"
    icon = "bi-plug"

    def _send(self, cmd: dict) -> dict:
        payload = json.dumps(cmd)
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(5)
                sock.connect((self.device_ip, 9999))
                sock.send(_encrypt(payload))
                data = sock.recv(4096)
                return json.loads(_decrypt(data))
        except Exception as exc:
            return {"error": str(exc)}

    def get_state(self) -> dict[str, Any]:
        return self._send({"system": {"get_sysinfo": {}}})

    def get_capabilities(self) -> list[dict[str, Any]]:
        return [
            {"command": "turn_on",  "description": "Turn plug on",  "params": []},
            {"command": "turn_off", "description": "Turn plug off", "params": []},
            {"command": "get_info", "description": "Get device info", "params": []},
        ]

    def execute(self, command: str, params: dict[str, Any]) -> dict[str, Any]:
        if command == "turn_on":
            return self._send({"system": {"set_relay_state": {"state": 1}}})
        if command == "turn_off":
            return self._send({"system": {"set_relay_state": {"state": 0}}})
        if command == "get_info":
            return self.get_state()
        return {"ok": False, "error": f"Unknown command '{command}'"}
