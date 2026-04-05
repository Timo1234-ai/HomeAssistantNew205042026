"""WLAN network manager: list available networks and show current connection."""

from __future__ import annotations

import logging
import subprocess
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


def _split_nmcli(line: str) -> list[str]:
    """Split a nmcli terse-mode line respecting backslash-escaped colons.

    nmcli uses ``:`` as field separator in ``-t`` mode and escapes literal
    colons inside values as ``\\:``.  This helper splits on unescaped colons
    and then removes the escape from remaining values.
    """
    parts = re.split(r"(?<!\\):", line)
    return [p.replace(r"\:", ":") for p in parts]


@dataclass
class WlanNetwork:
    """Represents a scanned WLAN access-point."""
    ssid: str
    bssid: str = ""
    signal: int = 0          # dBm
    channel: int = 0
    security: str = ""
    frequency: str = ""


@dataclass
class WlanStatus:
    """Current WLAN connection state."""
    connected: bool = False
    ssid: str = ""
    bssid: str = ""
    ip_address: str = ""
    interface: str = ""
    signal: int = 0
    networks: list[WlanNetwork] = field(default_factory=list)


class WlanManager:
    """Scan for and connect to WLAN networks.

    Uses operating-system CLI tools (``iwlist``, ``nmcli``) so no root
    credentials need to be stored in the application.
    """

    def __init__(self, interface: Optional[str] = None) -> None:
        self.interface = interface or self._detect_interface()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_status(self) -> WlanStatus:
        """Return current WLAN connection status."""
        status = WlanStatus(interface=self.interface)
        try:
            status = self._nmcli_status(status)
        except Exception:
            try:
                status = self._iwconfig_status(status)
            except Exception as exc:
                logger.debug("Could not determine WLAN status: %s", exc)
        return status

    def scan_networks(self) -> list[WlanNetwork]:
        """Return a list of visible WLAN access-points."""
        networks: list[WlanNetwork] = []
        try:
            networks = self._nmcli_scan()
        except Exception:
            try:
                networks = self._iwlist_scan()
            except Exception as exc:
                logger.debug("Network scan failed: %s", exc)
        return networks

    def connect(self, ssid: str, password: str = "") -> bool:
        """Connect to an SSID using nmcli."""
        # Validate SSID to prevent command-line injection
        if not ssid or len(ssid) > 32:
            logger.error("Invalid SSID (empty or too long)")
            return False
        # Allow only printable ASCII characters that are safe for CLI use
        if not all(0x20 <= ord(c) <= 0x7E for c in ssid):
            logger.error("SSID contains non-printable characters")
            return False
        try:
            cmd = ["nmcli", "device", "wifi", "connect", ssid]
            if password:
                cmd += ["password", password]
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30
            )
            return result.returncode == 0
        except Exception as exc:
            logger.error("Failed to connect to %s: %s", ssid, exc)
            return False

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_interface() -> str:
        """Return first available wireless interface."""
        try:
            out = subprocess.check_output(
                ["iw", "dev"], text=True, timeout=5
            )
            for line in out.splitlines():
                m = re.search(r"Interface\s+(\S+)", line)
                if m:
                    return m.group(1)
        except Exception:
            pass
        return "wlan0"

    def _nmcli_status(self, status: WlanStatus) -> WlanStatus:
        out = subprocess.check_output(
            ["nmcli", "-t", "-f", "ACTIVE,SSID,BSSID,SIGNAL,DEVICE",
             "device", "wifi"],
            text=True, timeout=5
        )
        for line in out.splitlines():
            parts = _split_nmcli(line)
            if len(parts) >= 5 and parts[0] == "yes":
                status.connected = True
                status.ssid = parts[1]
                status.bssid = parts[2]
                status.signal = int(parts[3]) if parts[3].isdigit() else 0
                status.interface = parts[4]
                break
        if status.connected:
            status.ip_address = self._get_ip(status.interface)
        return status

    def _nmcli_scan(self) -> list[WlanNetwork]:
        out = subprocess.check_output(
            ["nmcli", "-t", "-f", "SSID,BSSID,SIGNAL,CHAN,SECURITY,FREQ",
             "device", "wifi", "list"],
            text=True, timeout=10
        )
        networks: list[WlanNetwork] = []
        for line in out.splitlines():
            parts = _split_nmcli(line)
            if len(parts) >= 6 and parts[0]:
                try:
                    networks.append(WlanNetwork(
                        ssid=parts[0],
                        bssid=parts[1],
                        signal=int(parts[2]) if parts[2].isdigit() else 0,
                        channel=int(parts[3]) if parts[3].isdigit() else 0,
                        security=parts[4],
                        frequency=parts[5],
                    ))
                except (ValueError, IndexError):
                    continue
        return networks

    def _iwconfig_status(self, status: WlanStatus) -> WlanStatus:
        out = subprocess.check_output(
            ["iwconfig", self.interface], text=True,
            stderr=subprocess.DEVNULL, timeout=5
        )
        if "ESSID:" in out:
            m = re.search(r'ESSID:"([^"]*)"', out)
            if m:
                status.ssid = m.group(1)
                status.connected = bool(status.ssid)
            m = re.search(r"Signal level=(-?\d+)", out)
            if m:
                status.signal = int(m.group(1))
            status.interface = self.interface
            if status.connected:
                status.ip_address = self._get_ip(self.interface)
        return status

    def _iwlist_scan(self) -> list[WlanNetwork]:
        out = subprocess.check_output(
            ["iwlist", self.interface, "scan"],
            text=True, stderr=subprocess.DEVNULL, timeout=15
        )
        networks: list[WlanNetwork] = []
        current: dict = {}
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("Cell"):
                if current.get("ssid"):
                    networks.append(WlanNetwork(**current))
                current = {}
                m = re.search(r"Address: (\S+)", line)
                if m:
                    current["bssid"] = m.group(1)
            elif line.startswith("ESSID:"):
                m = re.search(r'ESSID:"([^"]*)"', line)
                current["ssid"] = m.group(1) if m else ""
            elif "Frequency:" in line:
                m = re.search(r"Frequency:(\S+)", line)
                current["frequency"] = m.group(1) if m else ""
                m = re.search(r"Channel:(\d+)", line)
                current["channel"] = int(m.group(1)) if m else 0
            elif "Signal level=" in line:
                m = re.search(r"Signal level=(-?\d+)", line)
                current["signal"] = int(m.group(1)) if m else 0
            elif "Encryption key:" in line:
                current["security"] = "WPA2" if "on" in line else "Open"
        if current.get("ssid"):
            networks.append(WlanNetwork(**current))
        return networks

    @staticmethod
    def _get_ip(interface: str) -> str:
        try:
            out = subprocess.check_output(
                ["ip", "-4", "addr", "show", interface],
                text=True, timeout=5
            )
            m = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", out)
            return m.group(1) if m else ""
        except Exception:
            return ""
