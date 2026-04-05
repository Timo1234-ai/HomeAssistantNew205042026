"""Generic HTTP device plugin.

Works with any device that exposes a REST/HTTP API.
"""

from __future__ import annotations

from typing import Any

import requests

from home_assistant.devices.plugin_manager import DevicePlugin


class GenericHttpPlugin(DevicePlugin):
    """Control any device with a REST HTTP API."""

    name = "HTTP Device"
    plugin_id = "generic_http"
    icon = "bi-hdd-network"

    def __init__(self, device_ip: str, **kwargs: Any) -> None:
        super().__init__(device_ip, **kwargs)
        self.port: int = int(kwargs.get("port", 80))
        self.base_path: str = kwargs.get("base_path", "")

    @property
    def _base_url(self) -> str:
        return f"http://{self.device_ip}:{self.port}{self.base_path}"

    def get_state(self) -> dict[str, Any]:
        try:
            resp = requests.get(self._base_url, timeout=5)
            try:
                return {"status": resp.status_code, "data": resp.json()}
            except Exception:
                return {"status": resp.status_code, "text": resp.text[:200]}
        except Exception as exc:
            return {"error": str(exc)}

    def get_capabilities(self) -> list[dict[str, Any]]:
        return [
            {"command": "get",  "description": "HTTP GET",  "params": ["path"]},
            {"command": "post", "description": "HTTP POST", "params": ["path", "body"]},
            {"command": "put",  "description": "HTTP PUT",  "params": ["path", "body"]},
        ]

    def execute(self, command: str, params: dict[str, Any]) -> dict[str, Any]:
        path = params.get("path", "")
        url = f"{self._base_url}{path}"
        body = params.get("body", {})
        try:
            if command == "get":
                resp = requests.get(url, timeout=5)
            elif command == "post":
                resp = requests.post(url, json=body, timeout=5)
            elif command == "put":
                resp = requests.put(url, json=body, timeout=5)
            else:
                return {"ok": False, "error": f"Unknown command '{command}'"}
            try:
                return {"ok": resp.ok, "status": resp.status_code, "data": resp.json()}
            except Exception:
                return {"ok": resp.ok, "status": resp.status_code, "text": resp.text[:200]}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
