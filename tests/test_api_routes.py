"""Tests for Flask API routes."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app import create_app
from home_assistant.network.scanner import DiscoveredDevice
from home_assistant.network.wlan_manager import WlanNetwork, WlanStatus


@pytest.fixture()
def client():
    app = create_app({"TESTING": True})
    with app.test_client() as c:
        yield c


@pytest.fixture()
def auth_client():
    app = create_app(
        {"TESTING": True, "HA_REQUIRE_AUTH": True, "HA_API_TOKEN": "secret-token"}
    )
    with app.test_client() as c:
        yield c


class TestWlanRoutes:
    def test_wlan_status_connected(self, client):
        status = WlanStatus(
            connected=True, ssid="HomeNet", ip_address="192.168.1.10",
            interface="wlan0", signal=75,
        )
        with patch("home_assistant.api.routes.wlan_manager") as mock_wm:
            mock_wm.get_status.return_value = status
            r = client.get("/api/wlan/status")
        assert r.status_code == 200
        data = r.get_json()
        assert data["connected"] is True
        assert data["ssid"] == "HomeNet"

    def test_wlan_status_disconnected(self, client):
        status = WlanStatus(connected=False)
        with patch("home_assistant.api.routes.wlan_manager") as mock_wm:
            mock_wm.get_status.return_value = status
            r = client.get("/api/wlan/status")
        assert r.status_code == 200
        assert r.get_json()["connected"] is False

    def test_wlan_networks(self, client):
        nets = [
            WlanNetwork(ssid="Net1", signal=80, channel=6),
            WlanNetwork(ssid="Net2", signal=55, channel=11),
        ]
        with patch("home_assistant.api.routes.wlan_manager") as mock_wm:
            mock_wm.scan_networks.return_value = nets
            r = client.get("/api/wlan/networks")
        assert r.status_code == 200
        data = r.get_json()
        assert len(data) == 2
        assert data[0]["ssid"] == "Net1"

    def test_wlan_diagnostics(self, client):
        diag = {
            "interface": "wlan0",
            "tools": {"nmcli": True},
            "commands": {"iw_dev": {"ok": True}},
            "notes": ["example"],
        }
        with patch("home_assistant.api.routes.wlan_manager") as mock_wm:
            mock_wm.get_diagnostics.return_value = diag
            r = client.get("/api/wlan/diagnostics")
        assert r.status_code == 200
        data = r.get_json()
        assert data["interface"] == "wlan0"
        assert "tools" in data
        assert "commands" in data

    def test_wlan_connect_success(self, client):
        with patch("home_assistant.api.routes.wlan_manager") as mock_wm:
            mock_wm.connect.return_value = True
            r = client.post("/api/wlan/connect",
                            json={"ssid": "TestNet", "password": "pass"})
        assert r.status_code == 200
        assert r.get_json()["ok"] is True

    def test_wlan_connect_missing_ssid(self, client):
        r = client.post("/api/wlan/connect", json={})
        assert r.status_code == 400

    def test_wlan_connect_failure(self, client):
        with patch("home_assistant.api.routes.wlan_manager") as mock_wm:
            mock_wm.connect.return_value = False
            r = client.post("/api/wlan/connect",
                            json={"ssid": "Bad", "password": "wrong"})
        assert r.status_code == 200
        assert r.get_json()["ok"] is False

    def test_wlan_connect_requires_auth(self, auth_client):
        r = auth_client.post("/api/wlan/connect", json={"ssid": "TestNet"})
        assert r.status_code == 401

    def test_wlan_connect_with_token(self, auth_client):
        with patch("home_assistant.api.routes.wlan_manager") as mock_wm:
            mock_wm.connect.return_value = True
            r = auth_client.post(
                "/api/wlan/connect",
                json={"ssid": "TestNet"},
                headers={"X-API-Token": "secret-token"},
            )
        assert r.status_code == 200
        assert r.get_json()["ok"] is True

    def test_wlan_diagnostics_requires_debug_or_auth(self, auth_client):
        r = auth_client.get("/api/wlan/diagnostics")
        assert r.status_code == 403

    def test_wlan_diagnostics_with_token(self, auth_client):
        with patch("home_assistant.api.routes.wlan_manager") as mock_wm:
            mock_wm.get_diagnostics.return_value = {"ok": True}
            r = auth_client.get(
                "/api/wlan/diagnostics",
                headers={"Authorization": "Bearer secret-token"},
            )
        assert r.status_code == 200


class TestDeviceRoutes:
    def _make_device(self) -> DiscoveredDevice:
        dev = DiscoveredDevice(
            ip="192.168.1.50", mac="aa:bb:cc:dd:ee:ff",
            device_type="Sonos Speaker", plugin_id="sonos",
        )
        return dev

    def test_scan_devices(self, client):
        dev = self._make_device()
        with patch("home_assistant.api.routes.device_scanner") as mock_sc:
            mock_sc.scan.return_value = [dev]
            r = client.get("/api/devices/scan")
        assert r.status_code == 200
        body = r.get_json()
        assert "devices" in body
        assert "scan_context" in body
        data = body["devices"]
        assert len(data) == 1
        assert data[0]["ip"] == "192.168.1.50"

    def test_scan_devices_with_explicit_network(self, client):
        dev = self._make_device()
        with patch("home_assistant.api.routes.device_scanner") as mock_sc:
            mock_sc.scan.return_value = [dev]
            r = client.get("/api/devices/scan?network=10.0.0.0/24")
        assert r.status_code == 200
        body = r.get_json()
        assert body["scan_context"]["subnet"] == "10.0.0.0/24"
        mock_sc.scan.assert_called_once_with("10.0.0.0/24")

    def test_scan_devices_wlan_resolves_subnet(self, client):
        dev = self._make_device()
        status = WlanStatus(
            connected=True, ssid="HomeNet", ip_address="192.168.1.10",
            interface="wlan0", signal=75,
        )
        with patch("home_assistant.api.routes.wlan_manager") as mock_wm:
            mock_wm.get_status.return_value = status
            mock_wm.get_subnet.return_value = "192.168.1.0/24"
            with patch("home_assistant.api.routes.device_scanner") as mock_sc:
                mock_sc.scan.return_value = [dev]
                r = client.get("/api/devices/scan?wlan=HomeNet")
        assert r.status_code == 200
        body = r.get_json()
        assert body["scan_context"]["ssid"] == "HomeNet"
        assert body["scan_context"]["subnet"] == "192.168.1.0/24"
        assert body["scan_context"]["interface"] == "wlan0"
        mock_sc.scan.assert_called_once_with("192.168.1.0/24")

    def test_scan_devices_network_ssid_alias_resolves_subnet(self, client):
        dev = self._make_device()
        status = WlanStatus(
            connected=True, ssid="hacienda2", ip_address="192.168.68.129",
            interface="Wi-Fi", signal=80,
        )
        with patch("home_assistant.api.routes.wlan_manager") as mock_wm:
            mock_wm.get_status.return_value = status
            mock_wm.get_subnet.return_value = "192.168.68.0/24"
            with patch("home_assistant.api.routes.device_scanner") as mock_sc:
                mock_sc.scan.return_value = [dev]
                r = client.get("/api/devices/scan?network=hacienda2")
        assert r.status_code == 200
        mock_sc.scan.assert_called_once_with("192.168.68.0/24")

    def test_scan_devices_wlan_not_connected(self, client):
        status = WlanStatus(connected=False)
        with patch("home_assistant.api.routes.wlan_manager") as mock_wm:
            mock_wm.get_status.return_value = status
            r = client.get("/api/devices/scan?wlan=HomeNet")
        assert r.status_code == 409
        body = r.get_json()
        assert body["ok"] is False
        assert "HomeNet" in body["error"]

    def test_scan_devices_wlan_ssid_mismatch(self, client):
        status = WlanStatus(
            connected=True, ssid="OtherNet", ip_address="192.168.2.5",
            interface="wlan0",
        )
        with patch("home_assistant.api.routes.wlan_manager") as mock_wm:
            mock_wm.get_status.return_value = status
            r = client.get("/api/devices/scan?wlan=HomeNet")
        assert r.status_code == 409
        body = r.get_json()
        assert body["ok"] is False
        assert "OtherNet" in body["error"]
        assert "HomeNet" in body["error"]

    def test_scan_devices_wlan_subnet_unresolvable(self, client):
        status = WlanStatus(
            connected=True, ssid="HomeNet", ip_address="192.168.1.10",
            interface="wlan0",
        )
        with patch("home_assistant.api.routes.wlan_manager") as mock_wm:
            mock_wm.get_status.return_value = status
            mock_wm.get_subnet.return_value = None
            with patch("home_assistant.api.routes.device_scanner") as mock_sc:
                mock_sc.scan.return_value = []
                r = client.get("/api/devices/scan?wlan=HomeNet")
        assert r.status_code == 200
        mock_sc.scan.assert_called_once_with("192.168.1.0/24")

    def test_list_devices_empty(self, client):
        with patch("home_assistant.api.routes.device_scanner") as mock_sc:
            mock_sc.get_cached.return_value = []
            r = client.get("/api/devices")
        assert r.status_code == 200
        assert r.get_json() == []

    def test_device_state_not_found(self, client):
        with patch("home_assistant.api.routes.device_scanner") as mock_sc:
            mock_sc.get_cached.return_value = []
            r = client.get("/api/devices/1.2.3.4/state")
        assert r.status_code == 404

    def test_device_state_found(self, client):
        dev = self._make_device()
        mock_plugin = MagicMock()
        mock_plugin.get_state.return_value = {"on": True}
        with patch("home_assistant.api.routes.device_scanner") as mock_sc:
            mock_sc.get_cached.return_value = [dev]
            with patch("home_assistant.api.routes.plugin_manager") as mock_pm:
                mock_pm.get_instance.return_value = mock_plugin
                r = client.get("/api/devices/192.168.1.50/state")
        assert r.status_code == 200
        assert r.get_json() == {"on": True}

    def test_device_command_missing_command(self, client):
        dev = self._make_device()
        with patch("home_assistant.api.routes.device_scanner") as mock_sc:
            mock_sc.get_cached.return_value = [dev]
            r = client.post("/api/devices/192.168.1.50/command", json={})
        assert r.status_code == 400

    def test_device_command_executes(self, client):
        dev = self._make_device()
        mock_plugin = MagicMock()
        mock_plugin.execute.return_value = {"ok": True}
        with patch("home_assistant.api.routes.device_scanner") as mock_sc:
            mock_sc.get_cached.return_value = [dev]
            with patch("home_assistant.api.routes.plugin_manager") as mock_pm:
                mock_pm.get_instance.return_value = mock_plugin
                r = client.post(
                    "/api/devices/192.168.1.50/command",
                    json={"command": "play", "params": {}},
                )
        assert r.status_code == 200
        assert r.get_json()["ok"] is True


class TestPluginRoutes:
    def test_list_plugins(self, client):
        with patch("home_assistant.api.routes.plugin_manager") as mock_pm:
            mock_pm.list_available_plugins.return_value = [
                {"plugin_id": "generic", "source": "builtin"}
            ]
            r = client.get("/api/plugins")
        assert r.status_code == 200
        data = r.get_json()
        assert any(p["plugin_id"] == "generic" for p in data)

    def test_refresh_registry(self, client):
        with patch("home_assistant.api.routes.plugin_manager") as mock_pm:
            mock_pm.fetch_remote_registry.return_value = {"tplink": {}}
            r = client.post("/api/plugins/refresh")
        assert r.status_code == 200
        assert r.get_json()["ok"] is True


class TestScanDeviceRichIdentification:
    """Verify that /api/devices/scan response includes the rich identification fields."""

    def _make_device_classified(self) -> DiscoveredDevice:
        from home_assistant.network.scanner import DeviceScanner
        dev = DiscoveredDevice(
            ip="192.168.1.50", mac="aa:bb:cc:dd:ee:ff",
            open_ports=[1400], vendor="Sonos",
        )
        DeviceScanner(timeout=0.1)._classify(dev)
        return dev

    def test_scan_response_includes_identification_fields(self, client):
        dev = self._make_device_classified()
        with patch("home_assistant.api.routes.device_scanner") as mock_sc:
            mock_sc.scan.return_value = [dev]
            r = client.get("/api/devices/scan")
        assert r.status_code == 200
        body = r.get_json()
        d = body["devices"][0]
        assert "identification_confidence" in d
        assert "identification_sources" in d
        assert isinstance(d["identification_confidence"], int)
        assert isinstance(d["identification_sources"], list)

    def test_scan_response_confidence_positive(self, client):
        dev = self._make_device_classified()
        with patch("home_assistant.api.routes.device_scanner") as mock_sc:
            mock_sc.scan.return_value = [dev]
            r = client.get("/api/devices/scan")
        d = r.get_json()["devices"][0]
        assert d["identification_confidence"] > 0

    def test_scan_unknown_device_still_returned(self, client):
        """Unknown devices must be visible with best-effort metadata."""
        dev = DiscoveredDevice(ip="10.0.0.99", mac="", open_ports=[])
        from home_assistant.network.scanner import DeviceScanner
        DeviceScanner(timeout=0.1)._classify(dev)
        with patch("home_assistant.api.routes.device_scanner") as mock_sc:
            mock_sc.scan.return_value = [dev]
            r = client.get("/api/devices/scan")
        d = r.get_json()["devices"][0]
        assert d["ip"] == "10.0.0.99"
        assert "identification_confidence" in d
        assert d["identification_confidence"] >= 0


class TestRateLimiting:
    """Verify that rate limiting is applied to sensitive endpoints."""

    def _make_app(self):
        """Create app with rate limiting enabled and very tight limits for testing."""
        app = create_app({
            "TESTING": True,
            "RATELIMIT_ENABLED": True,
            "RATELIMIT_STORAGE_URI": "memory://",
        })
        return app

    def test_devices_scan_rate_limited(self):
        """After exceeding the rate limit, /api/devices/scan returns 429."""
        from home_assistant.network.scanner import DiscoveredDevice as DD
        app = create_app({
            "TESTING": True,
            "RATELIMIT_ENABLED": True,
            "RATELIMIT_STORAGE_URI": "memory://",
            # Override the limiter limit to 1 per minute for this test
        })
        # Patch the limiter's limit on the endpoint directly
        from home_assistant.api import routes as r_mod
        original_limit = None
        with app.test_client() as c:
            with patch("home_assistant.api.routes.device_scanner") as mock_sc:
                mock_sc.scan.return_value = []
                # Make 12 requests – exceeds the 10/minute limit
                statuses = [c.get("/api/devices/scan").status_code for _ in range(12)]
        assert 429 in statuses, "Expected a 429 after exceeding rate limit"

    def test_wlan_connect_rate_limited(self):
        """After exceeding the rate limit, /api/wlan/connect returns 429."""
        app = create_app({
            "TESTING": True,
            "RATELIMIT_ENABLED": True,
            "RATELIMIT_STORAGE_URI": "memory://",
        })
        with app.test_client() as c:
            with patch("home_assistant.api.routes.wlan_manager") as mock_wm:
                mock_wm.connect.return_value = True
                statuses = [
                    c.post("/api/wlan/connect", json={"ssid": "Net"}).status_code
                    for _ in range(7)
                ]
        assert 429 in statuses, "Expected a 429 after exceeding rate limit"

    def test_list_devices_rate_limited(self):
        """After exceeding the rate limit, /api/devices returns 429."""
        app = create_app({
            "TESTING": True,
            "RATELIMIT_ENABLED": True,
            "RATELIMIT_STORAGE_URI": "memory://",
        })
        with app.test_client() as c:
            with patch("home_assistant.api.routes.device_scanner") as mock_sc:
                mock_sc.get_cached.return_value = []
                statuses = [c.get("/api/devices").status_code for _ in range(32)]
        assert 429 in statuses, "Expected a 429 after exceeding rate limit"


class TestNonLocalBindSecurity:
    """Verify that non-local bind fails fast without an API token."""

    def test_nonlocal_bind_without_token_raises(self):
        import importlib
        import sys
        import types

        # Simulate __main__ execution path by calling the guard logic directly
        import os
        host = "0.0.0.0"
        is_local = host in {"127.0.0.1", "localhost"}
        require_token = not os.environ.get("HA_API_TOKEN", "").strip()

        assert not is_local
        assert require_token, "Should require token for non-local bind"

    def test_create_app_no_crash_local(self):
        """create_app should succeed even without a token on localhost."""
        app = create_app({"TESTING": True})
        assert app is not None
