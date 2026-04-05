"""REST API routes for the Home Assistant application."""

from __future__ import annotations

import logging
from typing import Any

from flask import Blueprint, jsonify, request

from home_assistant.devices.plugin_manager import PluginManager
from home_assistant.network.scanner import DeviceScanner
from home_assistant.network.wlan_manager import WlanManager

logger = logging.getLogger(__name__)

api = Blueprint("api", __name__, url_prefix="/api")

# Shared singletons (initialised in app.py)
wlan_manager: WlanManager = WlanManager()
device_scanner: DeviceScanner = DeviceScanner()
plugin_manager: PluginManager = PluginManager()


# -----------------------------------------------------------------------
# WLAN endpoints
# -----------------------------------------------------------------------

@api.get("/wlan/status")
def wlan_status() -> Any:
    """Return current WLAN connection status."""
    status = wlan_manager.get_status()
    return jsonify({
        "connected":  status.connected,
        "ssid":       status.ssid,
        "bssid":      status.bssid,
        "ip_address": status.ip_address,
        "interface":  status.interface,
        "signal":     status.signal,
    })


@api.get("/wlan/networks")
def wlan_networks() -> Any:
    """Return list of available WLAN networks."""
    networks = wlan_manager.scan_networks()
    return jsonify([
        {
            "ssid":      n.ssid,
            "bssid":     n.bssid,
            "signal":    n.signal,
            "channel":   n.channel,
            "security":  n.security,
            "frequency": n.frequency,
        }
        for n in networks
    ])


@api.get("/wlan/diagnostics")
def wlan_diagnostics() -> Any:
    """Return WLAN diagnostic details for troubleshooting scan issues."""
    return jsonify(wlan_manager.get_diagnostics())


@api.post("/wlan/connect")
def wlan_connect() -> Any:
    """Connect to a WLAN network."""
    data = request.get_json(force=True) or {}
    ssid = data.get("ssid", "")
    password = data.get("password", "")
    if not ssid:
        return jsonify({"ok": False, "error": "ssid is required"}), 400
    ok = wlan_manager.connect(ssid, password)
    return jsonify({"ok": ok})


# -----------------------------------------------------------------------
# Device scan endpoints
# -----------------------------------------------------------------------

@api.get("/devices/scan")
def scan_devices() -> Any:
    """Trigger a network scan and return discovered devices."""
    network = request.args.get("network")
    devices = device_scanner.scan(network or None)
    return jsonify([d.to_dict() for d in devices])


@api.get("/devices")
def list_devices() -> Any:
    """Return cached (last scanned) device list."""
    devices = device_scanner.get_cached()
    return jsonify([d.to_dict() for d in devices])


# -----------------------------------------------------------------------
# Device control endpoints
# -----------------------------------------------------------------------

@api.get("/devices/<ip>/state")
def device_state(ip: str) -> Any:
    """Return current state of a device."""
    devices = device_scanner.get_cached()
    dev = next((d for d in devices if d.ip == ip), None)
    if dev is None:
        return jsonify({"error": "Device not found"}), 404
    plugin = plugin_manager.get_instance(dev.plugin_id, ip)
    return jsonify(plugin.get_state())


@api.get("/devices/<ip>/capabilities")
def device_capabilities(ip: str) -> Any:
    """Return supported commands for a device."""
    devices = device_scanner.get_cached()
    dev = next((d for d in devices if d.ip == ip), None)
    if dev is None:
        return jsonify({"error": "Device not found"}), 404
    plugin = plugin_manager.get_instance(dev.plugin_id, ip)
    return jsonify(plugin.get_capabilities())


@api.post("/devices/<ip>/command")
def device_command(ip: str) -> Any:
    """Execute a command on a device."""
    devices = device_scanner.get_cached()
    dev = next((d for d in devices if d.ip == ip), None)
    if dev is None:
        return jsonify({"error": "Device not found"}), 404
    data = request.get_json(force=True) or {}
    command = data.get("command", "")
    params = data.get("params", {})
    if not command:
        return jsonify({"ok": False, "error": "command is required"}), 400
    plugin = plugin_manager.get_instance(dev.plugin_id, ip)
    result = plugin.execute(command, params)
    return jsonify(result)


# -----------------------------------------------------------------------
# Plugin endpoints
# -----------------------------------------------------------------------

@api.get("/plugins")
def list_plugins() -> Any:
    """Return all available plugins (built-in + remote registry)."""
    return jsonify(plugin_manager.list_available_plugins())


@api.post("/plugins/refresh")
def refresh_registry() -> Any:
    """Force-refresh the remote plugin registry."""
    registry = plugin_manager.fetch_remote_registry(force=True)
    return jsonify({"ok": True, "count": len(registry)})
