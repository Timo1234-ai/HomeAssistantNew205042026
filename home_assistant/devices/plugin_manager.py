"""Device plugin manager.

Responsibilities:
  1. Maintain a registry of built-in plugins.
  2. Fetch additional plugin manifests from the internet plugin registry.
  3. Dynamically load and instantiate plugin classes.
  4. Provide a unified interface to send commands to devices.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urljoin

import requests

logger = logging.getLogger(__name__)

# URL of the remote plugin registry (JSON manifest)
# The actual registry URL is built in _remote_registry_url() below.

# Fallback built-in plugin registry (plugin_id → module path inside package)
BUILTIN_PLUGINS: dict[str, str] = {
    "philips_hue":   "home_assistant.devices.plugins.philips_hue",
    "sonos":         "home_assistant.devices.plugins.sonos",
    "lifx":          "home_assistant.devices.plugins.lifx",
    "chromecast":    "home_assistant.devices.plugins.chromecast",
    "mqtt":          "home_assistant.devices.plugins.mqtt_device",
    "generic_http":  "home_assistant.devices.plugins.generic_http",
    "generic":       "home_assistant.devices.plugins.generic",
    "generic_camera":"home_assistant.devices.plugins.generic",
    "homekit":       "home_assistant.devices.plugins.generic",
    "home_assistant":"home_assistant.devices.plugins.generic_http",
}

# Remote registry cache
_REMOTE_REGISTRY: dict[str, Any] = {}
_REGISTRY_FETCHED_AT: float = 0.0
_REGISTRY_TTL: float = 3600.0  # refresh every hour


class DevicePlugin:
    """Base class for all device plugins."""

    #: Human-readable name shown in the UI
    name: str = "Generic Device"
    #: Plugin identifier (matches ``DiscoveredDevice.plugin_id``)
    plugin_id: str = "generic"
    #: Icon class (Bootstrap Icons)
    icon: str = "bi-device-hdd"

    def __init__(self, device_ip: str, **kwargs: Any) -> None:
        self.device_ip = device_ip
        self.config: dict[str, Any] = kwargs

    # ------------------------------------------------------------------
    # Override in subclasses
    # ------------------------------------------------------------------

    def get_state(self) -> dict[str, Any]:
        """Return current state of the device."""
        return {}

    def execute(self, command: str, params: dict[str, Any]) -> dict[str, Any]:
        """Execute a command on the device."""
        return {"ok": False, "error": "Not implemented"}

    def get_capabilities(self) -> list[dict[str, Any]]:
        """Return list of supported commands / capabilities."""
        return []


class PluginManager:
    """Loads and manages device plugins.

    Built-in plugins are in ``home_assistant.devices.plugins.*``.
    Remote plugins are downloaded as Python source and executed in a
    sandboxed module namespace.
    """

    def __init__(self, cache_dir: Optional[str] = None) -> None:
        self._plugins: dict[str, type[DevicePlugin]] = {}
        self._instances: dict[str, DevicePlugin] = {}
        self.cache_dir = Path(cache_dir or Path.home() / ".home_assistant" / "plugins")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._load_builtins()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_plugin_class(self, plugin_id: str) -> type[DevicePlugin]:
        """Return plugin class for *plugin_id*, fetching it if necessary."""
        if plugin_id not in self._plugins:
            self._try_load_remote(plugin_id)
        return self._plugins.get(plugin_id, DevicePlugin)

    def get_instance(
        self, plugin_id: str, device_ip: str, **kwargs: Any
    ) -> DevicePlugin:
        """Return a plugin instance for a specific device IP."""
        key = f"{plugin_id}::{device_ip}"
        if key not in self._instances:
            cls = self.get_plugin_class(plugin_id)
            self._instances[key] = cls(device_ip, **kwargs)
        return self._instances[key]

    def fetch_remote_registry(self, force: bool = False) -> dict[str, Any]:
        """Download the remote plugin registry and return it."""
        global _REMOTE_REGISTRY, _REGISTRY_FETCHED_AT
        now = time.time()
        if (
            not force
            and _REMOTE_REGISTRY
            and now - _REGISTRY_FETCHED_AT < _REGISTRY_TTL
        ):
            return _REMOTE_REGISTRY

        registry_url = os.environ.get(
            "HA_PLUGIN_REGISTRY_URL", _remote_registry_url()
        )
        try:
            resp = requests.get(registry_url, timeout=10)
            resp.raise_for_status()
            _REMOTE_REGISTRY = resp.json()
            _REGISTRY_FETCHED_AT = now
            logger.info("Fetched remote plugin registry (%d entries)", len(_REMOTE_REGISTRY))
        except Exception as exc:
            logger.warning("Could not fetch remote plugin registry: %s", exc)
            _REMOTE_REGISTRY = {}

        return _REMOTE_REGISTRY

    def list_available_plugins(self) -> list[dict[str, str]]:
        """Return metadata for all known plugins (built-in + remote)."""
        result = [
            {"plugin_id": pid, "source": "builtin"}
            for pid in BUILTIN_PLUGINS
        ]
        registry = self.fetch_remote_registry()
        for pid, meta in registry.items():
            if pid not in BUILTIN_PLUGINS:
                result.append({"plugin_id": pid, "source": "remote", **meta})
        return result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _load_builtins(self) -> None:
        for plugin_id, module_path in BUILTIN_PLUGINS.items():
            try:
                mod = importlib.import_module(module_path)
                cls = self._find_plugin_class(mod)
                if cls:
                    self._plugins[plugin_id] = cls
                    logger.debug("Loaded built-in plugin '%s'", plugin_id)
            except Exception as exc:
                logger.debug("Could not load built-in plugin '%s': %s", plugin_id, exc)

    def _try_load_remote(self, plugin_id: str) -> None:
        """Try to download and load a plugin from the remote registry."""
        # Check cached file first
        cached = self.cache_dir / f"{plugin_id}.py"
        if cached.exists():
            self._load_from_file(plugin_id, cached)
            if plugin_id in self._plugins:
                return

        # Download from remote registry
        registry = self.fetch_remote_registry()
        if plugin_id not in registry:
            logger.debug("Plugin '%s' not found in remote registry", plugin_id)
            return

        meta = registry[plugin_id]
        url = meta.get("url")
        if not url:
            return

        try:
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            cached.write_text(resp.text, encoding="utf-8")
            self._load_from_file(plugin_id, cached)
            logger.info("Downloaded and loaded remote plugin '%s'", plugin_id)
        except Exception as exc:
            logger.warning("Failed to download plugin '%s': %s", plugin_id, exc)

    def _load_from_file(self, plugin_id: str, path: Path) -> None:
        """Load a plugin class from a Python file."""
        try:
            spec = importlib.util.spec_from_file_location(
                f"ha_plugin_{plugin_id}", path
            )
            if spec is None or spec.loader is None:
                return
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
            cls = self._find_plugin_class(mod)
            if cls:
                self._plugins[plugin_id] = cls
        except Exception as exc:
            logger.warning("Failed to load plugin from %s: %s", path, exc)

    @staticmethod
    def _find_plugin_class(mod: Any) -> Optional[type[DevicePlugin]]:
        """Find the first DevicePlugin subclass in a module."""
        for attr in dir(mod):
            obj = getattr(mod, attr)
            try:
                if (
                    isinstance(obj, type)
                    and issubclass(obj, DevicePlugin)
                    and obj is not DevicePlugin
                ):
                    return obj
            except TypeError:
                pass
        return None


def _remote_registry_url() -> str:
    """Return the URL of the remote plugin registry JSON file."""
    return (
        "https://raw.githubusercontent.com/"
        "Timo1234-ai/HomeAssistantNew205042026/main/plugin_registry.json"
    )
