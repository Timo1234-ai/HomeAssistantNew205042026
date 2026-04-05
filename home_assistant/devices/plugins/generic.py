"""Generic fallback device plugin."""

from __future__ import annotations

from typing import Any

from home_assistant.devices.plugin_manager import DevicePlugin


class GenericPlugin(DevicePlugin):
    """Minimal fallback plugin for unrecognised devices."""

    name = "Generic Device"
    plugin_id = "generic"
    icon = "bi-cpu"

    def get_state(self) -> dict[str, Any]:
        return {"ip": self.device_ip, "status": "discovered"}

    def get_capabilities(self) -> list[dict[str, Any]]:
        return [
            {"command": "ping", "description": "Check device reachability", "params": []},
        ]

    def execute(self, command: str, params: dict[str, Any]) -> dict[str, Any]:
        import subprocess
        if command == "ping":
            try:
                result = subprocess.run(
                    ["ping", "-c", "1", "-W", "1", self.device_ip],
                    capture_output=True, timeout=5
                )
                return {"ok": result.returncode == 0, "reachable": result.returncode == 0}
            except Exception as exc:
                return {"ok": False, "error": str(exc)}
        return {"ok": False, "error": f"Unknown command '{command}'"}
