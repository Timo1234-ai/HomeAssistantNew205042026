"""Chromecast plugin.

Communicates via the Google Cast protocol (port 8009) using pychromecast
if available, otherwise falls back to mDNS info.
"""

from __future__ import annotations

from typing import Any

from home_assistant.devices.plugin_manager import DevicePlugin


class ChromecastPlugin(DevicePlugin):
    """Control a Google Chromecast device."""

    name = "Chromecast"
    plugin_id = "chromecast"
    icon = "bi-cast"

    def get_state(self) -> dict[str, Any]:
        try:
            import pychromecast  # type: ignore
            casts, browser = pychromecast.get_listed_chromecasts(
                friendly_names=[], known_hosts=[self.device_ip]
            )
            if casts:
                cast = casts[0]
                cast.wait()
                return {
                    "name": cast.name,
                    "model": cast.model_name,
                    "status": cast.status.display_name,
                    "is_idle": cast.is_idle,
                }
            return {"error": "Device not found"}
        except ImportError:
            return {"info": "pychromecast not installed – limited control"}
        except Exception as exc:
            return {"error": str(exc)}

    def get_capabilities(self) -> list[dict[str, Any]]:
        return [
            {"command": "get_state", "description": "Get device state", "params": []},
        ]

    def execute(self, command: str, params: dict[str, Any]) -> dict[str, Any]:
        if command == "get_state":
            return self.get_state()
        return {"ok": False, "error": f"Unknown command '{command}'"}
