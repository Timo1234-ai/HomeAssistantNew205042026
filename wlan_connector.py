"""WLAN network connector module.

Connects the system to a Wi-Fi network using nmcli.
"""

import subprocess
from typing import Optional


class WlanConnectionError(Exception):
    """Raised when a Wi-Fi connection attempt fails."""


def connect_to_network(ssid: str, password: Optional[str] = None) -> None:
    """Connect to a Wi-Fi network identified by *ssid*.

    If the network uses open authentication, *password* can be *None* or an
    empty string.  Otherwise a WPA/WPA2 passphrase must be supplied.

    Args:
        ssid: The SSID of the target network.
        password: The WPA passphrase, or *None* / empty string for open networks.

    Raises:
        WlanConnectionError: If the connection attempt fails.
        RuntimeError: If nmcli is not installed.
    """
    if not ssid:
        raise ValueError("SSID must not be empty.")

    cmd = ["nmcli", "device", "wifi", "connect", ssid]

    if password:
        cmd += ["password", password]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "nmcli is not installed. Please install NetworkManager to use this feature."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise WlanConnectionError(
            f"Connection to '{ssid}' timed out after 60 seconds."
        ) from exc

    if result.returncode != 0:
        error_msg = result.stderr.strip() or result.stdout.strip()
        raise WlanConnectionError(
            f"Failed to connect to '{ssid}': {error_msg}"
        )


def disconnect_from_network(interface: str = "wlan0") -> None:
    """Disconnect the Wi-Fi interface.

    Args:
        interface: The network interface name (default: ``wlan0``).

    Raises:
        WlanConnectionError: If the disconnect attempt fails.
        RuntimeError: If nmcli is not installed.
    """
    cmd = ["nmcli", "device", "disconnect", interface]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "nmcli is not installed. Please install NetworkManager to use this feature."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise WlanConnectionError(
            f"Disconnect from '{interface}' timed out after 30 seconds."
        ) from exc

    if result.returncode != 0:
        error_msg = result.stderr.strip() or result.stdout.strip()
        raise WlanConnectionError(
            f"Failed to disconnect from '{interface}': {error_msg}"
        )
