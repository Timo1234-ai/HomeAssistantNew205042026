"""Shelly smart relay/sensor plugin (community).

Communicates with Shelly devices via their local HTTP API.
Docs: https://shelly-api-docs.shelly.cloud/gen1/
"""

from __future__ import annotations

from typing import Any

import requests

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


class ShellyPlugin(DevicePlugin):
    """Control a Shelly smart relay."""

    name = "Shelly"
    plugin_id = "shelly"
    icon = "bi-toggle-on"

    @property
    def _base(self) -> str:
        return f"http://{self.device_ip}"

    def get_state(self) -> dict[str, Any]:
        try:
            return requests.get(f"{self._base}/status", timeout=5).json()
        except Exception as exc:
            return {"error": str(exc)}

    def get_capabilities(self) -> list[dict[str, Any]]:
        return [
            {"command": "turn_on",  "description": "Turn relay on",  "params": ["channel"]},
            {"command": "turn_off", "description": "Turn relay off", "params": ["channel"]},
            {"command": "toggle",   "description": "Toggle relay",   "params": ["channel"]},
            {"command": "get_info", "description": "Get device info", "params": []},
        ]

    def execute(self, command: str, params: dict[str, Any]) -> dict[str, Any]:
        channel = int(params.get("channel", 0))
        try:
            if command == "get_info":
                return self.get_state()
            action_map = {"turn_on": "on", "turn_off": "off", "toggle": "toggle"}
            if command in action_map:
                resp = requests.get(
                    f"{self._base}/relay/{channel}",
                    params={"turn": action_map[command]},
                    timeout=5,
                )
                return {"ok": resp.ok, "result": resp.json()}
            return {"ok": False, "error": f"Unknown command '{command}'"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
