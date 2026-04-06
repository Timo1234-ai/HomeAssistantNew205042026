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
            r = client.get("/api/devices/scan?wlan=HomeNet")
        assert r.status_code == 500
        body = r.get_json()
        assert body["ok"] is False

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
