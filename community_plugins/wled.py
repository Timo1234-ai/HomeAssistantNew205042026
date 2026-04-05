"""WLED LED strip controller plugin (community).

Communicates with WLED firmware via its JSON HTTP API.
Docs: https://kno.wled.ge/interfaces/json-api/
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


class WledPlugin(DevicePlugin):
    """Control a WLED LED strip controller."""

    name = "WLED"
    plugin_id = "wled"
    icon = "bi-lightbulb-fill"

    @property
    def _api(self) -> str:
        return f"http://{self.device_ip}/json"

    def get_state(self) -> dict[str, Any]:
        try:
            return requests.get(f"{self._api}/state", timeout=5).json()
        except Exception as exc:
            return {"error": str(exc)}

    def get_capabilities(self) -> list[dict[str, Any]]:
        return [
            {"command": "turn_on",    "description": "Turn strip on",    "params": []},
            {"command": "turn_off",   "description": "Turn strip off",   "params": []},
            {"command": "set_color",  "description": "Set RGB colour",   "params": ["r", "g", "b"]},
            {"command": "set_effect", "description": "Set effect ID",    "params": ["effect_id"]},
            {"command": "set_brightness", "description": "Set brightness (0–255)", "params": ["brightness"]},
        ]

    def execute(self, command: str, params: dict[str, Any]) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if command == "turn_on":
            body = {"on": True}
        elif command == "turn_off":
            body = {"on": False}
        elif command == "set_color":
            body = {"seg": [{"col": [[int(params.get("r", 255)),
                                      int(params.get("g", 255)),
                                      int(params.get("b", 255))]]}]}
        elif command == "set_effect":
            body = {"seg": [{"fx": int(params.get("effect_id", 0))}]}
        elif command == "set_brightness":
            body = {"bri": int(params.get("brightness", 128))}
        else:
            return {"ok": False, "error": f"Unknown command '{command}'"}

        try:
            resp = requests.post(f"{self._api}/state", json=body, timeout=5)
            return {"ok": resp.ok, "status": resp.status_code}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
