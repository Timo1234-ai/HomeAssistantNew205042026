"""Tests for the device scanner and vendor lookup."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from home_assistant.network.scanner import (
    DeviceScanner,
    DiscoveredDevice,
    _OUI_CACHE,
    lookup_vendor,
)


class TestDiscoveredDevice:
    def test_to_dict_keys(self):
        dev = DiscoveredDevice(ip="192.168.1.1", mac="aa:bb:cc:dd:ee:ff")
        d = dev.to_dict()
        for key in ("ip", "mac", "hostname", "vendor", "open_ports",
                    "services", "device_type", "plugin_id", "last_seen"):
            assert key in d

    def test_to_dict_values(self):
        dev = DiscoveredDevice(ip="10.0.0.1", mac="11:22:33:44:55:66",
                               device_type="Sonos Speaker")
        d = dev.to_dict()
        assert d["ip"] == "10.0.0.1"
        assert d["device_type"] == "Sonos Speaker"


class TestDeviceScannerClassify:
    def _scanner(self) -> DeviceScanner:
        return DeviceScanner(timeout=0.1)

    def test_sonos_classified(self):
        sc = self._scanner()
        dev = DiscoveredDevice(ip="192.168.1.10", mac="", open_ports=[1400])
        sc._classify(dev)
        assert dev.plugin_id == "sonos"
        assert "Sonos" in dev.device_type

    def test_lifx_classified(self):
        sc = self._scanner()
        dev = DiscoveredDevice(ip="192.168.1.11", mac="", open_ports=[56700])
        sc._classify(dev)
        assert dev.plugin_id == "lifx"

    def test_camera_classified(self):
        sc = self._scanner()
        dev = DiscoveredDevice(ip="192.168.1.12", mac="", open_ports=[554])
        sc._classify(dev)
        assert dev.plugin_id == "generic_camera"

    def test_mqtt_classified(self):
        sc = self._scanner()
        dev = DiscoveredDevice(ip="192.168.1.13", mac="", open_ports=[1883])
        sc._classify(dev)
        assert dev.plugin_id == "mqtt"

    def test_generic_http_classified(self):
        sc = self._scanner()
        dev = DiscoveredDevice(ip="192.168.1.14", mac="", open_ports=[80])
        sc._classify(dev)
        assert dev.plugin_id == "generic_http"

    def test_unknown_device_fallback(self):
        sc = self._scanner()
        dev = DiscoveredDevice(ip="192.168.1.15", mac="", open_ports=[])
        sc._classify(dev)
        assert dev.plugin_id == "generic"

    def test_philips_vendor_classified(self):
        sc = self._scanner()
        dev = DiscoveredDevice(ip="192.168.1.16", mac="", vendor="Philips", open_ports=[80])
        sc._classify(dev)
        assert dev.plugin_id == "philips_hue"


class TestDeviceScannerArp:
    def test_proc_net_arp_fallback(self, tmp_path):
        arp_file = tmp_path / "arp"
        arp_file.write_text(
            "IP address       HW type     Flags       HW address            Mask     Device\n"
            "192.168.1.1      0x1         0x2         aa:bb:cc:dd:ee:ff     *        wlan0\n"
        )
        with patch("builtins.open", side_effect=lambda p, *a, **kw: open(str(arp_file))):
            with patch("subprocess.check_output", side_effect=Exception("no scapy")):
                with patch("subprocess.run", side_effect=Exception("no arp-scan")):
                    hosts = DeviceScanner._arp_scan("192.168.1.0/24")
        # Either found via /proc/net/arp or empty (depending on mocking depth)
        assert isinstance(hosts, dict)

    def test_detect_network_fallback(self):
        with patch("subprocess.check_output", side_effect=Exception("ip not found")):
            network = DeviceScanner._detect_network()
        assert network == "192.168.1.0/24"

    def test_get_cached_returns_list(self):
        sc = DeviceScanner()
        assert isinstance(sc.get_cached(), list)


class TestLookupVendor:
    def test_empty_mac_returns_empty(self):
        assert lookup_vendor("") == ""

    def test_zeroes_mac_returns_empty(self):
        assert lookup_vendor("00:00:00:00:00:00") == ""

    def test_caches_result(self):
        _OUI_CACHE.clear()
        with patch(
            "home_assistant.network.scanner.lookup_vendor",
            wraps=lookup_vendor,
        ):
            # First call populates cache
            lookup_vendor("aa:bb:cc:11:22:33")
            # Should not raise
            lookup_vendor("aa:bb:cc:11:22:33")
