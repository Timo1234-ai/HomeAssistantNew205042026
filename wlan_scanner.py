"""WLAN network scanner module.

Scans for available Wi-Fi networks on 2.4 GHz and 5 GHz bands using nmcli.
"""

import subprocess
import re
from dataclasses import dataclass, field
from typing import List, Optional


# Frequency boundaries (in MHz)
BAND_24GHZ_MAX_MHZ = 2500
BAND_5GHZ_MIN_MHZ = 5000


@dataclass
class WlanNetwork:
    """Represents a discovered WLAN network."""

    ssid: str
    bssid: str
    frequency_mhz: int
    signal_strength: int  # dBm or percentage (0-100)
    security: str

    @property
    def band(self) -> str:
        """Return the frequency band as a human-readable string."""
        if self.frequency_mhz < BAND_24GHZ_MAX_MHZ:
            return "2.4 GHz"
        if self.frequency_mhz >= BAND_5GHZ_MIN_MHZ:
            return "5 GHz"
        return "Unknown"

    @property
    def channel(self) -> Optional[int]:
        """Return the Wi-Fi channel derived from the frequency."""
        if self.frequency_mhz < BAND_24GHZ_MAX_MHZ:
            # 2.4 GHz channels: channel = (freq - 2412) / 5 + 1
            return (self.frequency_mhz - 2412) // 5 + 1
        if self.frequency_mhz >= BAND_5GHZ_MIN_MHZ:
            # 5 GHz channels: channel = (freq - 5000) / 5
            return (self.frequency_mhz - 5000) // 5
        return None


def _parse_nmcli_output(raw_output: str) -> List[WlanNetwork]:
    """Parse the output of *nmcli -t -f* into a list of WlanNetwork objects."""
    networks: List[WlanNetwork] = []
    seen_ssids: set = set()

    for line in raw_output.splitlines():
        # nmcli separates fields with ':'; escaped colons inside values are '\:'
        # We split on unescaped ':' only.
        parts = re.split(r"(?<!\\):", line)
        if len(parts) < 6:
            continue

        ssid = parts[0].replace("\\:", ":").strip()
        bssid = parts[1].replace("\\:", ":").strip()
        mode = parts[2].strip()
        freq_str = parts[3].strip()
        signal_str = parts[4].strip()
        security = parts[5].strip()

        # Skip hidden networks (empty SSID) and non-infrastructure mode entries
        if not ssid or mode.lower() not in ("infra", "infrastructure"):
            continue

        try:
            frequency_mhz = int(freq_str)
        except ValueError:
            continue

        # Only include 2.4 GHz and 5 GHz networks
        if not (frequency_mhz < BAND_24GHZ_MAX_MHZ or frequency_mhz >= BAND_5GHZ_MIN_MHZ):
            continue

        try:
            signal = int(signal_str)
        except ValueError:
            signal = 0

        # Deduplicate by SSID (keep strongest signal)
        if ssid in seen_ssids:
            for net in networks:
                if net.ssid == ssid and signal > net.signal_strength:
                    net.signal_strength = signal
                    net.frequency_mhz = frequency_mhz
                    net.bssid = bssid
                    break
            continue

        seen_ssids.add(ssid)
        networks.append(
            WlanNetwork(
                ssid=ssid,
                bssid=bssid,
                frequency_mhz=frequency_mhz,
                signal_strength=signal,
                security=security,
            )
        )

    # Sort: strongest signal first
    networks.sort(key=lambda n: n.signal_strength, reverse=True)
    return networks


def scan_networks(rescan: bool = True) -> List[WlanNetwork]:
    """Scan for available WLAN networks and return a list of WlanNetwork objects.

    Args:
        rescan: When *True* (default) force a fresh scan before listing.

    Returns:
        A list of discovered WlanNetwork objects sorted by signal strength
        (strongest first).

    Raises:
        RuntimeError: If nmcli is not available or returns a non-zero exit code.
    """
    rescan_flag = "yes" if rescan else "no"
    cmd = [
        "nmcli",
        "--terse",
        "--fields", "SSID,BSSID,MODE,FREQ,SIGNAL,SECURITY",
        "device", "wifi", "list",
        "--rescan", rescan_flag,
    ]

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
        raise RuntimeError("Wi-Fi scan timed out after 30 seconds.") from exc

    if result.returncode != 0:
        error_msg = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"Wi-Fi scan failed: {error_msg}")

    return _parse_nmcli_output(result.stdout)
