"""Sonos Speaker plugin.

Uses the Sonos UPnP / SOAP API (port 1400).
"""

from __future__ import annotations

import re
from typing import Any
from xml.etree import ElementTree as ET

import requests

from home_assistant.devices.plugin_manager import DevicePlugin

_TRANSPORT_URL = "http://{ip}:1400/MediaRenderer/AVTransport/Control"
_RENDERING_URL = "http://{ip}:1400/MediaRenderer/RenderingControl/Control"

_SOAP_ACTION = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"'
    ' s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
    "<s:Body>{body}</s:Body>"
    "</s:Envelope>"
)


class SonosPlugin(DevicePlugin):
    """Control a Sonos speaker."""

    name = "Sonos"
    plugin_id = "sonos"
    icon = "bi-speaker"

    def get_state(self) -> dict[str, Any]:
        try:
            resp = requests.get(
                f"http://{self.device_ip}:1400/status/topology",
                timeout=5
            )
            return {"xml": resp.text[:500]}
        except Exception as exc:
            return {"error": str(exc)}

    def get_capabilities(self) -> list[dict[str, Any]]:
        return [
            {"command": "play",  "description": "Start playback", "params": []},
            {"command": "pause", "description": "Pause playback", "params": []},
            {"command": "stop",  "description": "Stop playback",  "params": []},
            {"command": "set_volume", "description": "Set volume (0-100)", "params": ["volume"]},
        ]

    def execute(self, command: str, params: dict[str, Any]) -> dict[str, Any]:
        dispatch = {
            "play":       ("AVTransport", "Play",  "<InstanceID>0</InstanceID><Speed>1</Speed>"),
            "pause":      ("AVTransport", "Pause", "<InstanceID>0</InstanceID>"),
            "stop":       ("AVTransport", "Stop",  "<InstanceID>0</InstanceID>"),
        }
        if command in dispatch:
            svc, action, args = dispatch[command]
            return self._soap(svc, action, args)
        if command == "set_volume":
            vol = int(params.get("volume", 50))
            return self._soap(
                "RenderingControl",
                "SetVolume",
                f"<InstanceID>0</InstanceID>"
                f"<Channel>Master</Channel>"
                f"<DesiredVolume>{vol}</DesiredVolume>",
            )
        return {"ok": False, "error": f"Unknown command '{command}'"}

    def _soap(self, service: str, action: str, args: str) -> dict[str, Any]:
        ns_map = {
            "AVTransport":      "urn:schemas-upnp-org:service:AVTransport:1",
            "RenderingControl": "urn:schemas-upnp-org:service:RenderingControl:1",
        }
        url_map = {
            "AVTransport":      _TRANSPORT_URL.format(ip=self.device_ip),
            "RenderingControl": _RENDERING_URL.format(ip=self.device_ip),
        }
        ns = ns_map[service]
        url = url_map[service]
        body = (
            f'<u:{action} xmlns:u="{ns}">{args}</u:{action}>'
        )
        envelope = _SOAP_ACTION.format(body=body)
        headers = {
            "Content-Type": 'text/xml; charset="utf-8"',
            "SOAPACTION": f'"{ns}#{action}"',
        }
        try:
            resp = requests.post(url, data=envelope, headers=headers, timeout=5)
            return {"ok": resp.ok, "status": resp.status_code}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
