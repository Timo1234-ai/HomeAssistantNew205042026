"""LIFX Light plugin.

Communicates with LIFX lights using their local LAN UDP protocol (port 56700).
Docs: https://lan.developer.lifx.com/docs/
"""

from __future__ import annotations

import socket
import struct
from typing import Any

import requests

from home_assistant.devices.plugin_manager import DevicePlugin

_LIFX_PORT = 56700

# Minimal LIFX packet header fields
_HEADER_FMT = "<HHI8sHH4s"


def _build_packet(msg_type: int, payload: bytes = b"") -> bytes:
    """Build a minimal LIFX LAN protocol packet."""
    size = 36 + len(payload)
    protocol = 1024
    frame = struct.pack(
        "<HH",
        size,
        (protocol & 0x0FFF) | (0 << 12),  # tagged=0
    )
    frame_addr = b"\x00" * 8 + b"\x00\x00\x00\x00\x00\x00" + struct.pack("<BB", 0, 0)
    protocol_header = struct.pack("<QHH", 0, msg_type, 0)
    return frame + frame_addr + protocol_header + payload


class LifxPlugin(DevicePlugin):
    """Control a LIFX smart light bulb."""

    name = "LIFX Light"
    plugin_id = "lifx"
    icon = "bi-lightbulb-fill"

    def get_state(self) -> dict[str, Any]:
        # Try HTTP API first (LIFX Cloud — requires token, skip if unavailable)
        # Fall back to LAN ping
        try:
            packet = _build_packet(101)  # GetLight
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.settimeout(2)
                sock.sendto(packet, (self.device_ip, _LIFX_PORT))
                data, _ = sock.recvfrom(256)
                return {"raw_response": data.hex(), "reachable": True}
        except Exception as exc:
            return {"reachable": False, "error": str(exc)}

    def get_capabilities(self) -> list[dict[str, Any]]:
        return [
            {"command": "turn_on",  "description": "Turn light on",  "params": []},
            {"command": "turn_off", "description": "Turn light off", "params": []},
            {"command": "set_color", "description": "Set HSBK colour",
             "params": ["hue", "saturation", "brightness", "kelvin"]},
        ]

    def execute(self, command: str, params: dict[str, Any]) -> dict[str, Any]:
        if command == "turn_on":
            return self._set_power(65535)
        if command == "turn_off":
            return self._set_power(0)
        if command == "set_color":
            return self._set_color(params)
        return {"ok": False, "error": f"Unknown command '{command}'"}

    def _set_power(self, level: int) -> dict[str, Any]:
        payload = struct.pack("<HI", level, 0)  # level + duration
        packet = _build_packet(21, payload)  # SetPower
        return self._send(packet)

    def _set_color(self, params: dict[str, Any]) -> dict[str, Any]:
        hue = int(params.get("hue", 0))
        sat = int(params.get("saturation", 0))
        bri = int(params.get("brightness", 65535))
        kel = int(params.get("kelvin", 3500))
        duration = 0
        payload = struct.pack("<BHHHHHI", 0, hue, sat, bri, kel, 0, duration)
        packet = _build_packet(102, payload)  # SetColor
        return self._send(packet)

    def _send(self, packet: bytes) -> dict[str, Any]:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.settimeout(2)
                sock.sendto(packet, (self.device_ip, _LIFX_PORT))
                return {"ok": True}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
