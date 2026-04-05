"""Tests for the WLAN manager."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from home_assistant.network.wlan_manager import WlanManager, WlanNetwork, WlanStatus


class TestWlanManagerStatus:
    def test_returns_wlan_status_type(self):
        wm = WlanManager(interface="wlan0")
        with patch.object(wm, "_nmcli_status", side_effect=Exception("no nmcli")):
            with patch.object(wm, "_iwconfig_status", side_effect=Exception("no iwconfig")):
                status = wm.get_status()
        assert isinstance(status, WlanStatus)

    def test_nmcli_status_connected(self):
        # nmcli -t escapes colons in MAC as \:
        wm = WlanManager(interface="wlan0")
        nmcli_output = r"yes:MyWifi:AA\:BB\:CC\:DD\:EE\:FF:75:wlan0" + "\n"
        with patch("subprocess.check_output", return_value=nmcli_output):
            with patch.object(wm, "_get_ip", return_value="192.168.1.100"):
                status = wm._nmcli_status(WlanStatus(interface="wlan0"))
        assert status.connected is True
        assert status.ssid == "MyWifi"
        assert status.bssid == "AA:BB:CC:DD:EE:FF"
        assert status.signal == 75
        assert status.ip_address == "192.168.1.100"

    def test_nmcli_status_not_connected(self):
        wm = WlanManager(interface="wlan0")
        nmcli_output = r"no:OtherNet:AA\:BB\:CC\:DD\:EE\:FF:60:wlan0" + "\n"
        with patch("subprocess.check_output", return_value=nmcli_output):
            status = wm._nmcli_status(WlanStatus(interface="wlan0"))
        assert status.connected is False

    def test_iwconfig_status_connected(self):
        wm = WlanManager(interface="wlan0")
        iwconfig_output = 'wlan0  ESSID:"HomeNet"  Signal level=-55 dBm'
        with patch("subprocess.check_output", return_value=iwconfig_output):
            with patch.object(wm, "_get_ip", return_value="192.168.1.5"):
                status = wm._iwconfig_status(WlanStatus(interface="wlan0"))
        assert status.connected is True
        assert status.ssid == "HomeNet"
        assert status.signal == -55

    def test_get_ip_parses_correctly(self):
        output = "inet 192.168.1.42/24 brd 192.168.1.255 scope global wlan0"
        with patch("subprocess.check_output", return_value=output):
            ip = WlanManager._get_ip("wlan0")
        assert ip == "192.168.1.42"

    def test_get_ip_returns_empty_on_failure(self):
        with patch("subprocess.check_output", side_effect=Exception("fail")):
            ip = WlanManager._get_ip("wlan0")
        assert ip == ""


class TestWlanManagerScan:
    def test_nmcli_scan_parses_networks(self):
        # nmcli -t escapes colons in MAC as \:
        wm = WlanManager(interface="wlan0")
        output = (
            r"MySSID:AA\:BB\:CC\:DD\:EE\:FF:80:6:WPA2:2.4 GHz" + "\n"
            r"Other:11\:22\:33\:44\:55\:66:60:11:WPA2:5 GHz" + "\n"
        )
        with patch("subprocess.check_output", return_value=output):
            nets = wm._nmcli_scan()
        assert len(nets) == 2
        assert nets[0].ssid == "MySSID"
        assert nets[0].bssid == "AA:BB:CC:DD:EE:FF"
        assert nets[0].signal == 80
        assert nets[0].channel == 6

    def test_scan_falls_back_to_empty(self):
        wm = WlanManager(interface="wlan0")
        with patch.object(wm, "_nmcli_scan", side_effect=Exception("no nmcli")):
            with patch.object(wm, "_iwlist_scan", side_effect=Exception("no iwlist")):
                result = wm.scan_networks()
        assert result == []

    def test_connect_calls_nmcli(self):
        wm = WlanManager(interface="wlan0")
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            ok = wm.connect("TestNet", "password123")
        assert ok is True
        args = mock_run.call_args[0][0]
        assert "TestNet" in args
        assert "password123" in args

    def test_connect_returns_false_on_failure(self):
        wm = WlanManager(interface="wlan0")
        with patch("subprocess.run", side_effect=Exception("nmcli not found")):
            ok = wm.connect("TestNet", "pass")
        assert ok is False

    def test_connect_rejects_empty_ssid(self):
        wm = WlanManager(interface="wlan0")
        ok = wm.connect("")
        assert ok is False

    def test_connect_rejects_too_long_ssid(self):
        wm = WlanManager(interface="wlan0")
        ok = wm.connect("A" * 33)
        assert ok is False

    def test_connect_rejects_non_printable_ssid(self):
        wm = WlanManager(interface="wlan0")
        ok = wm.connect("Net\x00work")
        assert ok is False

    def test_get_diagnostics_contains_core_keys(self):
        wm = WlanManager(interface="wlan0")
        fake_result = MagicMock()
        fake_result.returncode = 0
        fake_result.stdout = "ok"
        fake_result.stderr = ""

        with patch("shutil.which", return_value="/usr/bin/tool"):
            with patch("subprocess.run", return_value=fake_result):
                diag = wm.get_diagnostics()

        assert diag["interface"] == "wlan0"
        assert "tools" in diag
        assert "commands" in diag
        assert "nmcli_wifi_list" in diag["commands"]

    def test_macos_scan_parses_networks(self):
        wm = WlanManager(interface="en0")
        airport_output = (
            "SSID BSSID RSSI CHANNEL HT CC SECURITY\n"
            "hacienda2 aa:bb:cc:dd:ee:ff -55 6 Y US WPA2(PSK/AES/AES)\n"
            "hacienda-iot 11:22:33:44:55:66 -62 11 Y US WPA2(PSK/AES/AES)\n"
        )
        with patch("subprocess.check_output", return_value=airport_output):
            nets = wm._macos_scan()
        assert len(nets) == 2
        assert nets[0].ssid == "hacienda2"
        assert nets[1].ssid == "hacienda-iot"

    def test_macos_status_parses_connected_network(self):
        wm = WlanManager(interface="en0")
        airport_output = (
            "     agrCtlRSSI: -49\n"
            "          BSSID: aa:bb:cc:dd:ee:ff\n"
            "           SSID: hacienda2\n"
        )
        with patch("subprocess.check_output", return_value=airport_output):
            with patch.object(wm, "_get_ip", return_value="192.168.1.20"):
                status = wm._macos_status(WlanStatus(interface="en0"))
        assert status.connected is True
        assert status.ssid == "hacienda2"
        assert status.ip_address == "192.168.1.20"
