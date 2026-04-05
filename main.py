"""Home Assistant – WLAN network search and connection.

Entry point for the WLAN scan-and-connect feature.  Run this script directly::

    python main.py

or after installing the package::

    home-assistant-wlan
"""

import getpass
import sys
from typing import List, Optional

from wlan_scanner import WlanNetwork, scan_networks
from wlan_connector import WlanConnectionError, connect_to_network


def display_networks(networks: List[WlanNetwork]) -> None:
    """Print a numbered table of discovered networks."""
    print()
    print(f"{'#':<4} {'SSID':<32} {'Band':<9} {'Signal':>6}  {'Security'}")
    print("-" * 65)
    for idx, net in enumerate(networks, start=1):
        print(
            f"{idx:<4} {net.ssid:<32} {net.band:<9} {net.signal_strength:>5}%  {net.security}"
        )
    print()


def prompt_network_selection(networks: List[WlanNetwork]) -> Optional[WlanNetwork]:
    """Ask the user to pick a network from the list and return it."""
    while True:
        raw = input(
            f"Enter the number of the network you want to connect to (1-{len(networks)}), "
            "or press Enter to skip: "
        ).strip()
        if raw == "":
            return None
        try:
            choice = int(raw)
            if 1 <= choice <= len(networks):
                return networks[choice - 1]
        except ValueError:
            pass
        print(f"  Invalid input. Please enter a number between 1 and {len(networks)}.")


def prompt_connect(network: WlanNetwork) -> bool:
    """Ask the user whether to connect to *network*."""
    while True:
        answer = input(
            f"Do you want to connect to '{network.ssid}' ({network.band})? [y/N] "
        ).strip().lower()
        if answer in ("y", "yes"):
            return True
        if answer in ("", "n", "no"):
            return False
        print("  Please answer 'y' or 'n'.")


def prompt_password(ssid: str) -> str:
    """Securely prompt for the Wi-Fi password (input is hidden)."""
    return getpass.getpass(f"Enter password for '{ssid}' (leave blank for open networks): ")


def run() -> int:
    """Main workflow: scan → display → select → confirm → connect.

    Returns:
        Exit code (0 = success, 1 = error, 2 = user aborted).
    """
    print("Scanning for Wi-Fi networks on 2.4 GHz and 5 GHz…")
    try:
        networks = scan_networks()
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if not networks:
        print("No Wi-Fi networks found.")
        return 0

    # Separate bands for reporting
    band_24 = [n for n in networks if n.band == "2.4 GHz"]
    band_5 = [n for n in networks if n.band == "5 GHz"]

    print(
        f"Found {len(networks)} network(s): "
        f"{len(band_24)} on 2.4 GHz, {len(band_5)} on 5 GHz."
    )
    display_networks(networks)

    selected = prompt_network_selection(networks)
    if selected is None:
        print("No network selected. Exiting.")
        return 2

    if not prompt_connect(selected):
        print("Connection cancelled.")
        return 2

    password = prompt_password(selected.ssid)

    print(f"Connecting to '{selected.ssid}'…")
    try:
        connect_to_network(selected.ssid, password or None)
    except (RuntimeError, WlanConnectionError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Successfully connected to '{selected.ssid}'.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
