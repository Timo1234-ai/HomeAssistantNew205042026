"""Philips Hue Bridge plugin.

Communicates with the local Philips Hue Bridge REST API.
Docs: https://developers.meethue.com/develop/hue-api/
"""

from __future__ import annotations

from typing import Any

import requests

from home_assistant.devices.plugin_manager import DevicePlugin


class PhilipsHuePlugin(DevicePlugin):
    """Control Philips Hue lights via the local Bridge API."""

    name = "Philips Hue"
    plugin_id = "philips_hue"
    icon = "bi-lightbulb"

    def __init__(self, device_ip: str, **kwargs: Any) -> None:
        super().__init__(device_ip, **kwargs)
        self.api_key: str = kwargs.get("api_key", "")
        self._base = f"http://{device_ip}/api"

    # ------------------------------------------------------------------

    @property
    def _api(self) -> str:
        return f"{self._base}/{self.api_key}" if self.api_key else self._base

    def get_state(self) -> dict[str, Any]:
        try:
            resp = requests.get(f"{self._api}/lights", timeout=5)
            return {"lights": resp.json()}
        except Exception as exc:
            return {"error": str(exc)}

    def get_capabilities(self) -> list[dict[str, Any]]:
        return [
            {
                "command": "set_light",
                "description": "Turn a light on/off and set brightness/colour",
                "params": ["light_id", "on", "brightness", "hue", "saturation"],
            },
            {
                "command": "get_lights",
                "description": "List all lights",
                "params": [],
            },
        ]

    def execute(self, command: str, params: dict[str, Any]) -> dict[str, Any]:
        if command == "get_lights":
            return self.get_state()
        if command == "set_light":
            return self._set_light(params)
        return {"ok": False, "error": f"Unknown command '{command}'"}

    def _set_light(self, params: dict[str, Any]) -> dict[str, Any]:
        light_id = params.get("light_id", 1)
        body: dict[str, Any] = {}
        if "on" in params:
            body["on"] = bool(params["on"])
        if "brightness" in params:
            body["bri"] = int(params["brightness"])
        if "hue" in params:
            body["hue"] = int(params["hue"])
        if "saturation" in params:
            body["sat"] = int(params["saturation"])
        try:
            resp = requests.put(
                f"{self._api}/lights/{light_id}/state",
                json=body, timeout=5
            )
            return {"ok": resp.ok, "result": resp.json()}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
