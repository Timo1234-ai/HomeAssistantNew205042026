"""Unit tests for main.py (run() workflow)."""

from io import StringIO
from unittest.mock import MagicMock, patch

import pytest

from main import display_networks, prompt_connect, prompt_network_selection, run
from wlan_scanner import WlanNetwork


def make_network(ssid="TestNet", freq=2412, signal=75, security="WPA2"):
    return WlanNetwork(ssid=ssid, bssid="AA:BB:CC:DD:EE:01", frequency_mhz=freq, signal_strength=signal, security=security)


class TestDisplayNetworks:
    def test_prints_header_and_rows(self, capsys):
        networks = [make_network("Alpha", 2412, 80), make_network("Beta", 5180, 60)]
        display_networks(networks)
        captured = capsys.readouterr()
        assert "Alpha" in captured.out
        assert "Beta" in captured.out
        assert "2.4 GHz" in captured.out
        assert "5 GHz" in captured.out


class TestPromptConnect:
    @patch("builtins.input", return_value="y")
    def test_returns_true_on_yes(self, _):
        assert prompt_connect(make_network()) is True

    @patch("builtins.input", return_value="n")
    def test_returns_false_on_no(self, _):
        assert prompt_connect(make_network()) is False

    @patch("builtins.input", return_value="")
    def test_returns_false_on_empty(self, _):
        assert prompt_connect(make_network()) is False

    @patch("builtins.input", side_effect=["maybe", "y"])
    def test_reprompts_on_invalid_input(self, _):
        assert prompt_connect(make_network()) is True


class TestPromptNetworkSelection:
    def test_returns_network_on_valid_choice(self):
        networks = [make_network("Alpha"), make_network("Beta")]
        with patch("builtins.input", return_value="2"):
            selected = prompt_network_selection(networks)
        assert selected.ssid == "Beta"

    def test_returns_none_on_empty_input(self):
        networks = [make_network()]
        with patch("builtins.input", return_value=""):
            selected = prompt_network_selection(networks)
        assert selected is None

    def test_reprompts_on_out_of_range(self):
        networks = [make_network("Alpha")]
        with patch("builtins.input", side_effect=["5", "1"]):
            selected = prompt_network_selection(networks)
        assert selected.ssid == "Alpha"

    def test_reprompts_on_non_numeric(self):
        networks = [make_network("Alpha")]
        with patch("builtins.input", side_effect=["abc", "1"]):
            selected = prompt_network_selection(networks)
        assert selected.ssid == "Alpha"


class TestRun:
    @patch("main.scan_networks", return_value=[])
    def test_run_no_networks_found(self, _, capsys):
        result = run()
        assert result == 0
        assert "No Wi-Fi networks found" in capsys.readouterr().out

    @patch("main.scan_networks", side_effect=RuntimeError("nmcli not found"))
    def test_run_scan_error_returns_1(self, _, capsys):
        result = run()
        assert result == 1

    @patch("main.connect_to_network")
    @patch("main.prompt_password", return_value="mypassword")
    @patch("main.prompt_connect", return_value=True)
    @patch("main.prompt_network_selection")
    @patch("main.scan_networks")
    def test_run_successful_connection(
        self, mock_scan, mock_select, mock_connect_prompt, mock_password, mock_connect, capsys
    ):
        net = make_network("HomeNet", 2412, 85)
        mock_scan.return_value = [net]
        mock_select.return_value = net
        run()
        mock_connect.assert_called_once_with("HomeNet", "mypassword")
        assert "Successfully connected" in capsys.readouterr().out

    @patch("main.prompt_network_selection", return_value=None)
    @patch("main.scan_networks", return_value=[make_network()])
    def test_run_no_selection_returns_2(self, _scan, _select):
        assert run() == 2

    @patch("main.prompt_connect", return_value=False)
    @patch("main.prompt_network_selection")
    @patch("main.scan_networks")
    def test_run_connection_declined_returns_2(self, mock_scan, mock_select, _):
        net = make_network()
        mock_scan.return_value = [net]
        mock_select.return_value = net
        assert run() == 2
