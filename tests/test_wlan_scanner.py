"""Unit tests for wlan_scanner.py."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from wlan_scanner import (
    BAND_24GHZ_MAX_MHZ,
    BAND_5GHZ_MIN_MHZ,
    WlanNetwork,
    _parse_nmcli_output,
    scan_networks,
)


# ---------------------------------------------------------------------------
# Sample nmcli --terse output
# ---------------------------------------------------------------------------

SAMPLE_NMCLI_OUTPUT = """\
HomeNetwork:AA\\:BB\\:CC\\:DD\\:EE\\:01:Infra:2412:82:WPA2
OfficeWifi:AA\\:BB\\:CC\\:DD\\:EE\\:02:Infra:5180:74:WPA2
GuestNetwork:AA\\:BB\\:CC\\:DD\\:EE\\:03:Infra:2437:60:--
Hidden::AA\\:BB\\:CC\\:DD\\:EE\\:04:Infra:2462:50:WPA2
AdHocNet:AA\\:BB\\:CC\\:DD\\:EE\\:05:Ad-Hoc:2412:40:--
"""


class TestWlanNetwork:
    """Tests for the WlanNetwork dataclass."""

    def test_band_24ghz(self):
        net = WlanNetwork("Test", "AA:BB:CC:DD:EE:01", 2412, 80, "WPA2")
        assert net.band == "2.4 GHz"

    def test_band_5ghz(self):
        net = WlanNetwork("Test", "AA:BB:CC:DD:EE:01", 5180, 75, "WPA2")
        assert net.band == "5 GHz"

    def test_band_unknown(self):
        net = WlanNetwork("Test", "AA:BB:CC:DD:EE:01", 3000, 50, "WPA2")
        assert net.band == "Unknown"

    def test_channel_24ghz(self):
        # 2412 MHz → channel 1
        net = WlanNetwork("Test", "AA:BB:CC:DD:EE:01", 2412, 80, "WPA2")
        assert net.channel == 1

    def test_channel_24ghz_6(self):
        # 2437 MHz → channel 6
        net = WlanNetwork("Test", "AA:BB:CC:DD:EE:01", 2437, 80, "WPA2")
        assert net.channel == 6

    def test_channel_5ghz(self):
        # 5180 MHz → channel 36
        net = WlanNetwork("Test", "AA:BB:CC:DD:EE:01", 5180, 75, "WPA2")
        assert net.channel == 36

    def test_channel_unknown_band(self):
        net = WlanNetwork("Test", "AA:BB:CC:DD:EE:01", 3000, 50, "WPA2")
        assert net.channel is None


class TestParseNmcliOutput:
    """Tests for the _parse_nmcli_output helper."""

    def test_parses_valid_24ghz_network(self):
        raw = "HomeNetwork:AA\\:BB\\:CC\\:DD\\:EE\\:01:Infra:2412:82:WPA2\n"
        networks = _parse_nmcli_output(raw)
        assert len(networks) == 1
        net = networks[0]
        assert net.ssid == "HomeNetwork"
        assert net.bssid == "AA:BB:CC:DD:EE:01"
        assert net.frequency_mhz == 2412
        assert net.band == "2.4 GHz"
        assert net.signal_strength == 82
        assert net.security == "WPA2"

    def test_parses_valid_5ghz_network(self):
        raw = "OfficeWifi:AA\\:BB\\:CC\\:DD\\:EE\\:02:Infra:5180:74:WPA2\n"
        networks = _parse_nmcli_output(raw)
        assert len(networks) == 1
        assert networks[0].band == "5 GHz"

    def test_skips_hidden_ssid(self):
        raw = ":AA\\:BB\\:CC\\:DD\\:EE\\:04:Infra:2462:50:WPA2\n"
        networks = _parse_nmcli_output(raw)
        assert networks == []

    def test_skips_adhoc_mode(self):
        raw = "AdHocNet:AA\\:BB\\:CC\\:DD\\:EE\\:05:Ad-Hoc:2412:40:--\n"
        networks = _parse_nmcli_output(raw)
        assert networks == []

    def test_skips_unknown_band(self):
        raw = "WeirdNet:AA\\:BB\\:CC\\:DD\\:EE\\:06:Infra:3000:55:WPA2\n"
        networks = _parse_nmcli_output(raw)
        assert networks == []

    def test_deduplicates_ssid_keeps_strongest(self):
        raw = (
            "HomeNetwork:AA\\:BB\\:CC\\:DD\\:EE\\:01:Infra:2412:60:WPA2\n"
            "HomeNetwork:AA\\:BB\\:CC\\:DD\\:EE\\:02:Infra:2437:90:WPA2\n"
        )
        networks = _parse_nmcli_output(raw)
        assert len(networks) == 1
        assert networks[0].signal_strength == 90

    def test_sorted_by_signal_strength_descending(self):
        raw = (
            "WeakNet:AA\\:BB\\:CC\\:DD\\:EE\\:01:Infra:2412:30:WPA2\n"
            "StrongNet:AA\\:BB\\:CC\\:DD\\:EE\\:02:Infra:5180:90:WPA2\n"
            "MedNet:AA\\:BB\\:CC\\:DD\\:EE\\:03:Infra:2437:60:WPA2\n"
        )
        networks = _parse_nmcli_output(raw)
        signals = [n.signal_strength for n in networks]
        assert signals == sorted(signals, reverse=True)

    def test_empty_output(self):
        assert _parse_nmcli_output("") == []

    def test_malformed_lines_skipped(self):
        raw = "not:enough\n"
        assert _parse_nmcli_output(raw) == []


class TestScanNetworks:
    """Tests for the scan_networks public API."""

    @patch("wlan_scanner.subprocess.run")
    def test_returns_parsed_networks(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="HomeNetwork:AA\\:BB\\:CC\\:DD\\:EE\\:01:Infra:2412:82:WPA2\n",
            stderr="",
        )
        networks = scan_networks()
        assert len(networks) == 1
        assert networks[0].ssid == "HomeNetwork"

    @patch("wlan_scanner.subprocess.run")
    def test_calls_nmcli_with_rescan_yes(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        scan_networks(rescan=True)
        args = mock_run.call_args[0][0]
        assert "--rescan" in args
        idx = args.index("--rescan")
        assert args[idx + 1] == "yes"

    @patch("wlan_scanner.subprocess.run")
    def test_calls_nmcli_with_rescan_no(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        scan_networks(rescan=False)
        args = mock_run.call_args[0][0]
        idx = args.index("--rescan")
        assert args[idx + 1] == "no"

    @patch("wlan_scanner.subprocess.run", side_effect=FileNotFoundError)
    def test_raises_runtime_error_when_nmcli_missing(self, _mock_run):
        with pytest.raises(RuntimeError, match="nmcli is not installed"):
            scan_networks()

    @patch("wlan_scanner.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="nmcli", timeout=30))
    def test_raises_runtime_error_on_timeout(self, _mock_run):
        with pytest.raises(RuntimeError, match="timed out"):
            scan_networks()

    @patch("wlan_scanner.subprocess.run")
    def test_raises_runtime_error_on_nonzero_exit(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="Error: No Wi-Fi device found.",
        )
        with pytest.raises(RuntimeError, match="Wi-Fi scan failed"):
            scan_networks()

    @patch("wlan_scanner.subprocess.run")
    def test_returns_empty_list_when_no_networks(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        networks = scan_networks()
        assert networks == []
