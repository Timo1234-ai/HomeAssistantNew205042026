"""Unit tests for wlan_connector.py."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from wlan_connector import WlanConnectionError, connect_to_network, disconnect_from_network


class TestConnectToNetwork:
    """Tests for connect_to_network()."""

    @patch("wlan_connector.subprocess.run")
    def test_successful_connection_with_password(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="Device 'wlan0' successfully activated.", stderr="")
        connect_to_network("HomeNetwork", "secret123")
        args = mock_run.call_args[0][0]
        assert "connect" in args
        assert "HomeNetwork" in args
        assert "password" in args
        assert "secret123" in args

    @patch("wlan_connector.subprocess.run")
    def test_successful_connection_open_network(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        connect_to_network("OpenNetwork", password=None)
        args = mock_run.call_args[0][0]
        assert "password" not in args

    @patch("wlan_connector.subprocess.run")
    def test_successful_connection_empty_password(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        connect_to_network("OpenNetwork", password="")
        args = mock_run.call_args[0][0]
        assert "password" not in args

    @patch("wlan_connector.subprocess.run")
    def test_raises_connection_error_on_failure(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="Error: Connection activation failed.",
        )
        with pytest.raises(WlanConnectionError, match="Failed to connect"):
            connect_to_network("HomeNetwork", "wrongpass")

    @patch("wlan_connector.subprocess.run", side_effect=FileNotFoundError)
    def test_raises_runtime_error_when_nmcli_missing(self, _mock_run):
        with pytest.raises(RuntimeError, match="nmcli is not installed"):
            connect_to_network("HomeNetwork", "pass")

    @patch("wlan_connector.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="nmcli", timeout=60))
    def test_raises_connection_error_on_timeout(self, _mock_run):
        with pytest.raises(WlanConnectionError, match="timed out"):
            connect_to_network("HomeNetwork", "pass")

    def test_raises_value_error_for_empty_ssid(self):
        with pytest.raises(ValueError, match="SSID must not be empty"):
            connect_to_network("", "pass")


class TestDisconnectFromNetwork:
    """Tests for disconnect_from_network()."""

    @patch("wlan_connector.subprocess.run")
    def test_successful_disconnect(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        disconnect_from_network("wlan0")
        args = mock_run.call_args[0][0]
        assert "disconnect" in args
        assert "wlan0" in args

    @patch("wlan_connector.subprocess.run")
    def test_raises_connection_error_on_failure(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="Error: Device 'wlan0' not found.",
        )
        with pytest.raises(WlanConnectionError, match="Failed to disconnect"):
            disconnect_from_network("wlan0")

    @patch("wlan_connector.subprocess.run", side_effect=FileNotFoundError)
    def test_raises_runtime_error_when_nmcli_missing(self, _mock_run):
        with pytest.raises(RuntimeError, match="nmcli is not installed"):
            disconnect_from_network()

    @patch("wlan_connector.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="nmcli", timeout=30))
    def test_raises_connection_error_on_timeout(self, _mock_run):
        with pytest.raises(WlanConnectionError, match="timed out"):
            disconnect_from_network("wlan0")
