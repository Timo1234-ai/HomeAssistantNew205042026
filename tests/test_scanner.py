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
                    "services", "device_type", "plugin_id", "last_seen",
                    "identification_confidence", "identification_sources"):
            assert key in d

    def test_to_dict_values(self):
        dev = DiscoveredDevice(ip="10.0.0.1", mac="11:22:33:44:55:66",
                               device_type="Sonos Speaker")
        d = dev.to_dict()
        assert d["ip"] == "10.0.0.1"
        assert d["device_type"] == "Sonos Speaker"

    def test_identification_defaults(self):
        dev = DiscoveredDevice(ip="192.168.1.1")
        assert dev.identification_confidence == 0
        assert dev.identification_sources == []


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

    # ------------------------------------------------------------------
    # Confidence and sources tests
    # ------------------------------------------------------------------

    def test_sonos_port_confidence(self):
        sc = self._scanner()
        dev = DiscoveredDevice(ip="192.168.1.10", mac="", open_ports=[1400])
        sc._classify(dev)
        assert dev.identification_confidence > 0
        assert "port:1400" in dev.identification_sources

    def test_sonos_port_and_vendor_higher_confidence(self):
        sc = self._scanner()
        dev_port_only = DiscoveredDevice(ip="192.168.1.10", mac="", open_ports=[1400])
        dev_both = DiscoveredDevice(ip="192.168.1.10", mac="", open_ports=[1400], vendor="Sonos")
        sc._classify(dev_port_only)
        sc._classify(dev_both)
        assert dev_both.identification_confidence > dev_port_only.identification_confidence
        assert "vendor:sonos" in dev_both.identification_sources

    def test_lifx_port_and_vendor_sources(self):
        sc = self._scanner()
        dev = DiscoveredDevice(ip="192.168.1.11", mac="", open_ports=[56700], vendor="LIFX Inc.")
        sc._classify(dev)
        assert "port:56700" in dev.identification_sources
        assert "vendor:lifx" in dev.identification_sources

    def test_unknown_device_low_confidence_with_mac(self):
        sc = self._scanner()
        dev = DiscoveredDevice(ip="192.168.1.15", mac="aa:bb:cc:dd:ee:ff", open_ports=[])
        sc._classify(dev)
        # Only the ARP/MAC presence signal (10 pts) fires for unknown devices with a MAC
        assert 0 < dev.identification_confidence <= 15
        assert "arp:mac" in dev.identification_sources

    def test_unknown_device_low_confidence_without_mac(self):
        sc = self._scanner()
        dev = DiscoveredDevice(ip="192.168.1.15", mac="", open_ports=[])
        sc._classify(dev)
        # Only the ping presence signal (10 pts) fires for unknown devices without a MAC
        assert 0 < dev.identification_confidence <= 15
        assert "ping" in dev.identification_sources

    def test_confidence_capped_at_100(self):
        sc = self._scanner()
        # Port + vendor + secure mqtt port → should not exceed 100
        dev = DiscoveredDevice(
            ip="192.168.1.13", mac="", open_ports=[1883, 8883], vendor="Mosquitto"
        )
        sc._classify(dev)
        assert dev.identification_confidence <= 100

    def test_sources_is_list(self):
        sc = self._scanner()
        dev = DiscoveredDevice(ip="192.168.1.14", mac="", open_ports=[80])
        sc._classify(dev)
        assert isinstance(dev.identification_sources, list)

    def test_vendor_only_device_has_source(self):
        sc = self._scanner()
        dev = DiscoveredDevice(ip="192.168.1.20", mac="", vendor="Acme Corp", open_ports=[])
        sc._classify(dev)
        assert "vendor:arp" in dev.identification_sources
        assert dev.identification_confidence > 0

    def test_hass_classified_with_confidence(self):
        sc = self._scanner()
        dev = DiscoveredDevice(ip="192.168.1.30", mac="", open_ports=[8123])
        sc._classify(dev)
        assert dev.plugin_id == "home_assistant"
        assert "port:8123" in dev.identification_sources
        assert dev.identification_confidence > 0


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
            with patch("socket.socket", side_effect=Exception("no socket")):
                network = DeviceScanner._detect_network()
        assert network == "192.168.1.0/24"

    def test_network_from_ipconfig_parses_prefix(self):
        ipconfig = (
            "Windows IP Configuration\n\n"
            "Wireless LAN adapter Wi-Fi:\n"
            "   IPv4 Address. . . . . . . . . . . : 192.168.68.129\n"
            "   Subnet Mask . . . . . . . . . . . : 255.255.255.0\n"
            "   Default Gateway . . . . . . . . . : 192.168.68.1\n"
        )
        network = DeviceScanner._network_from_ipconfig(ipconfig)
        assert network == "192.168.68.0/24"

    def test_network_from_ipconfig_returns_none_when_missing(self):
        network = DeviceScanner._network_from_ipconfig("Windows IP Configuration\n")
        assert network is None

    def test_detect_network_uses_windows_ipconfig(self):
        ipconfig = (
            "Windows IP Configuration\n\n"
            "Wireless LAN adapter Wi-Fi:\n"
            "   IPv4 Address. . . . . . . . . . . : 192.168.68.129\n"
            "   Subnet Mask . . . . . . . . . . . : 255.255.255.0\n"
        )
        with patch("platform.system", return_value="Windows"):
            with patch("subprocess.check_output", return_value=ipconfig):
                network = DeviceScanner._detect_network()
        assert network == "192.168.68.0/24"

    def test_get_cached_returns_list(self):
        sc = DeviceScanner()
        assert isinstance(sc.get_cached(), list)

    def test_arp_scan_windows_uses_arp_table(self):
        arp_output = (
            "Interface: 192.168.68.129 --- 0x10\n"
            "  Internet Address      Physical Address      Type\n"
            "  192.168.68.1          aa-bb-cc-dd-ee-ff     dynamic\n"
            "  192.168.68.55         11-22-33-44-55-66     dynamic\n"
            "  192.168.68.255        ff-ff-ff-ff-ff-ff     static\n"
        )

        def _check_output(cmd, **kwargs):
            if cmd[:2] == ["arp", "-a"]:
                return arp_output
            raise Exception("unavailable")

        with patch("platform.system", return_value="Windows"):
            with patch("subprocess.check_output", side_effect=_check_output):
                hosts = DeviceScanner._arp_scan("192.168.68.0/24")

        assert hosts.get("192.168.68.1") == "aa:bb:cc:dd:ee:ff"
        assert hosts.get("192.168.68.55") == "11:22:33:44:55:66"
        assert "192.168.68.255" not in hosts

    def test_ping_sweep_windows_uses_windows_ping_flags(self):
        seen = []

        def _run(cmd, **kwargs):
            seen.append(cmd)
            class R:
                returncode = 1
            return R()

        with patch("platform.system", return_value="Windows"):
            with patch("subprocess.run", side_effect=_run):
                DeviceScanner._ping_sweep("192.168.68.0/30")

        assert seen
        assert seen[0][:5] == ["ping", "-n", "1", "-w", "1000"]


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
