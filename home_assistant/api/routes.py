"""REST API routes for the Home Assistant application."""

from __future__ import annotations

import ipaddress
import logging
import hmac
import re
from functools import wraps
from typing import Any

from flask import Blueprint, current_app, jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from home_assistant.devices.plugin_manager import PluginManager
from home_assistant.network.scanner import DeviceScanner
from home_assistant.network.wlan_manager import WlanManager

logger = logging.getLogger(__name__)

api = Blueprint("api", __name__, url_prefix="/api")

# Shared singletons (initialised in app.py)
wlan_manager: WlanManager = WlanManager()
device_scanner: DeviceScanner = DeviceScanner()
plugin_manager: PluginManager = PluginManager()

# Rate limiter – initialised with the Flask app in app.py via limiter.init_app(app).
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[],
    storage_uri="memory://",
)

# Compiled pattern for validating IPv4 addresses in URL path parameters.
_IPV4_RE = re.compile(r"^(?:\d{1,3}\.){3}\d{1,3}$")


def _is_valid_ipv4(value: str) -> bool:
    """Return True only if *value* is a well-formed, routable IPv4 address."""
    if not _IPV4_RE.match(value):
        return False
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


# -----------------------------------------------------------------------
# WLAN endpoints
# -----------------------------------------------------------------------


def _token_from_request() -> str:
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header.split(" ", 1)[1].strip()
    return request.headers.get("X-API-Token", "").strip()


def _is_authorized() -> bool:
    if not current_app.config.get("HA_REQUIRE_AUTH", True):
        return True
    expected = str(current_app.config.get("HA_API_TOKEN", "")).strip()
    if not expected:
        return False
    # Use a constant-time comparison to prevent timing-based token enumeration.
    return hmac.compare_digest(
        _token_from_request().encode("utf-8"),
        expected.encode("utf-8"),
    )


def require_api_auth(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if _is_authorized():
            return func(*args, **kwargs)
        if not current_app.config.get("HA_API_TOKEN"):
            return jsonify({"ok": False, "error": "API auth enabled but token is not configured"}), 503
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    return wrapper


def require_debug_or_auth(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if current_app.debug or _is_authorized():
            return func(*args, **kwargs)
        return jsonify({"ok": False, "error": "Diagnostics require debug mode or valid token"}), 403

    return wrapper

@api.get("/wlan/status")
@limiter.limit("60 per minute")
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
@limiter.limit("10 per minute")
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


@api.get("/wlan/availability")
def wlan_availability() -> Any:
    """Return lightweight readiness info for WLAN scanning on this host."""
    return jsonify(wlan_manager.get_scan_availability())


@api.get("/wlan/diagnostics")
@require_debug_or_auth
def wlan_diagnostics() -> Any:
    """Return WLAN diagnostic details for troubleshooting scan issues."""
    return jsonify(wlan_manager.get_diagnostics())


@api.post("/wlan/connect")
@require_api_auth
@limiter.limit("5 per minute")
def wlan_connect() -> Any:
    """Connect to a WLAN network."""
    data = request.get_json(force=True) or {}
    ssid = data.get("ssid", "")
    password = data.get("password", "")
    if not ssid:
        return jsonify({"ok": False, "error": "ssid is required"}), 400
    ok = wlan_manager.connect(ssid, password)
    if ok:
        return jsonify({"ok": True})

    error = str(wlan_manager.get_last_connect_error() or "Connection failed")
    return jsonify({"ok": False, "error": error})


# -----------------------------------------------------------------------
# Device scan endpoints
# -----------------------------------------------------------------------

@api.get("/devices/scan")
@require_api_auth
@limiter.limit("10 per minute")
def scan_devices() -> Any:
    """Trigger a network scan and return discovered devices.

    Optional query parameters:

    * ``network`` – explicit CIDR subnet, e.g. ``192.168.1.0/24``.
      Non-CIDR values are treated as SSID aliases for convenience.
    * ``wlan`` – SSID of the currently connected WLAN. When supplied the
      endpoint resolves the subnet from the active WLAN connection and uses
      it for the scan, returning the resolved context alongside the results.
    """
    network_raw = (request.args.get("network") or "").strip()
    network: str | None = network_raw or None
    ssid: str | None = (request.args.get("wlan") or "").strip() or None

    # Convenience: allow users to pass SSID in the network field.
    if ssid is None and network and "/" not in network:
        ssid = network
        network = None

    scan_context: dict[str, Any] = {}
    local_ip_to_exclude: str | None = None

    if ssid:
        status = wlan_manager.get_status()
        if not status.connected:
            return jsonify({
                "ok": False,
                "error": f"Connect to '{ssid}' first before scanning.",
            }), 409
        if status.ssid.strip().lower() != ssid.lower():
            return jsonify({
                "ok": False,
                "error": (
                    f"Currently connected to '{status.ssid}', not '{ssid}'. "
                    f"Connect to '{ssid}' first before scanning."
                ),
            }), 409

        subnet = wlan_manager.get_subnet()
        if not subnet and status.ip_address:
            try:
                derived = ipaddress.ip_network(f"{status.ip_address}/24", strict=False)
                subnet = f"{derived.network_address}/{derived.prefixlen}"
            except ValueError:
                subnet = None
        if not subnet:
            return jsonify({
                "ok": False,
                "error": (
                    f"Could not determine subnet for WLAN '{ssid}' "
                    f"(interface: {status.interface or 'unknown'})."
                ),
            }), 500

        network = subnet
        local_ip_to_exclude = status.ip_address or None
        scan_context = {
            "ssid": status.ssid,
            "interface": status.interface,
            "subnet": subnet,
        }
    elif network:
        try:
            ipaddress.ip_network(network, strict=False)
        except ValueError:
            return jsonify({
                "ok": False,
                "error": "Invalid subnet format. Use CIDR like 192.168.68.0/24.",
            }), 400
        scan_context["subnet"] = network

    exclude = {local_ip_to_exclude} if local_ip_to_exclude else None
    devices = device_scanner.scan(network, exclude_ips=exclude)
    if local_ip_to_exclude:
        devices = [d for d in devices if d.ip != local_ip_to_exclude]
    return jsonify({"devices": [d.to_dict() for d in devices], "scan_context": scan_context})


@api.get("/devices")
@limiter.limit("30 per minute")
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
    if not _is_valid_ipv4(ip):
        return jsonify({"error": "Invalid IP address"}), 400
    devices = device_scanner.get_cached()
    dev = next((d for d in devices if d.ip == ip), None)
    if dev is None:
        return jsonify({"error": "Device not found"}), 404
    plugin = plugin_manager.get_instance(dev.plugin_id, ip)
    return jsonify(plugin.get_state())


@api.get("/devices/<ip>/capabilities")
def device_capabilities(ip: str) -> Any:
    """Return supported commands for a device."""
    if not _is_valid_ipv4(ip):
        return jsonify({"error": "Invalid IP address"}), 400
    devices = device_scanner.get_cached()
    dev = next((d for d in devices if d.ip == ip), None)
    if dev is None:
        return jsonify({"error": "Device not found"}), 404
    plugin = plugin_manager.get_instance(dev.plugin_id, ip)
    return jsonify(plugin.get_capabilities())


@api.post("/devices/<ip>/command")
@require_api_auth
def device_command(ip: str) -> Any:
    """Execute a command on a device."""
    if not _is_valid_ipv4(ip):
        return jsonify({"error": "Invalid IP address"}), 400
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
@require_api_auth
def refresh_registry() -> Any:
    """Force-refresh the remote plugin registry."""
    registry = plugin_manager.fetch_remote_registry(force=True)
    return jsonify({"ok": True, "count": len(registry)})
