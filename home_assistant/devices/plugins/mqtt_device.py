"""MQTT device plugin.

Connects to an MQTT broker and exposes publish/subscribe controls.
"""

from __future__ import annotations

from typing import Any

from home_assistant.devices.plugin_manager import DevicePlugin


class MqttPlugin(DevicePlugin):
    """Interact with an MQTT broker."""

    name = "MQTT Broker"
    plugin_id = "mqtt"
    icon = "bi-diagram-3"

    def __init__(self, device_ip: str, **kwargs: Any) -> None:
        super().__init__(device_ip, **kwargs)
        self.port: int = int(kwargs.get("port", 1883))
        self.username: str = kwargs.get("username", "")
        self.password: str = kwargs.get("password", "")

    def get_state(self) -> dict[str, Any]:
        return {
            "broker": self.device_ip,
            "port": self.port,
            "info": "Connect and subscribe to topics to receive messages",
        }

    def get_capabilities(self) -> list[dict[str, Any]]:
        return [
            {
                "command": "publish",
                "description": "Publish a message to a topic",
                "params": ["topic", "message", "qos"],
            },
        ]

    def execute(self, command: str, params: dict[str, Any]) -> dict[str, Any]:
        if command == "publish":
            return self._publish(params)
        return {"ok": False, "error": f"Unknown command '{command}'"}

    def _publish(self, params: dict[str, Any]) -> dict[str, Any]:
        try:
            import paho.mqtt.publish as publish  # type: ignore
            auth = None
            if self.username:
                auth = {"username": self.username, "password": self.password}
            publish.single(
                params["topic"],
                payload=str(params.get("message", "")),
                hostname=self.device_ip,
                port=self.port,
                qos=int(params.get("qos", 0)),
                auth=auth,
            )
            return {"ok": True}
        except ImportError:
            return {"ok": False, "error": "paho-mqtt not installed"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
