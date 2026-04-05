"""WLAN network manager: list available networks and show current connection."""

from __future__ import annotations

import logging
import os
import platform
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


def _split_nmcli(line: str) -> list[str]:
    """Split a nmcli terse-mode line respecting backslash-escaped colons.

    nmcli uses ``:`` as field separator in ``-t`` mode and escapes literal
    colons inside values as ``\\:``. This helper splits on unescaped colons
    and then removes the escape from remaining values.
    """
    parts = re.split(r"(?<!\\):", line)
    return [p.replace(r"\:", ":") for p in parts]


@dataclass
class WlanNetwork:
    """Represents a scanned WLAN access-point."""

    ssid: str
    bssid: str = ""
    signal: int = 0
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

    Uses operating-system CLI tools. Linux primarily uses ``nmcli`` / ``iw*``
    tools, while macOS uses Apple's ``airport`` tooling.
    """

    AIRPORT_BINS = [
        "/System/Library/PrivateFrameworks/Apple80211.framework/Versions/Current/Resources/airport",
        "/System/Library/PrivateFrameworks/Apple80211.framework/Versions/A/Resources/airport",
    ]

    def __init__(self, interface: Optional[str] = None) -> None:
        self.interface = interface or self._detect_interface()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_status(self) -> WlanStatus:
        """Return current WLAN connection status."""
        status = WlanStatus(interface=self.interface)

        # Linux path first.
        try:
            status = self._nmcli_status(status)
        except Exception:
            try:
                status = self._iwconfig_status(status)
            except Exception as exc:
                logger.debug("Linux WLAN status fallback failed: %s", exc)

        # macOS fallback when Linux path cannot provide a connected status.
        if (not status.connected) and platform.system() == "Darwin":
            try:
                status = self._macos_status(status)
            except Exception as exc:
                logger.debug("Could not determine macOS WLAN status: %s", exc)
        return status

    def scan_networks(self) -> list[WlanNetwork]:
        """Return a list of visible WLAN access-points."""
        networks: list[WlanNetwork] = []

        # Linux path first.
        try:
            networks = self._nmcli_scan()
        except Exception:
            try:
                networks = self._iwlist_scan()
            except Exception as exc:
                logger.debug("Linux WLAN scan fallback failed: %s", exc)

        # macOS fallback when Linux tools are unavailable.
        if (not networks) and platform.system() == "Darwin":
            try:
                networks = self._macos_scan()
            except Exception as exc:
                logger.debug("macOS WLAN scan failed: %s", exc)
        return networks

    def connect(self, ssid: str, password: str = "") -> bool:
        """Connect to an SSID using nmcli on Linux."""
        if not ssid or len(ssid) > 32:
            logger.error("Invalid SSID (empty or too long)")
            return False
        if not all(0x20 <= ord(c) <= 0x7E for c in ssid):
            logger.error("SSID contains non-printable characters")
            return False

        try:
            cmd = ["nmcli", "device", "wifi", "connect", ssid]
            if password:
                cmd += ["password", password]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            return result.returncode == 0
        except Exception as exc:
            logger.error("Failed to connect to %s: %s", ssid, exc)
            return False

    def get_diagnostics(self) -> dict:
        """Return WLAN scan diagnostics for the current runtime host."""
        interface = self.interface or ""
        airport_bin = self._macos_airport_bin()
        diagnostics = {
            "platform": platform.system(),
            "interface": interface,
            "tools": {
                "nmcli": shutil.which("nmcli") is not None,
                "iw": shutil.which("iw") is not None,
                "iwconfig": shutil.which("iwconfig") is not None,
                "iwlist": shutil.which("iwlist") is not None,
                "ip": shutil.which("ip") is not None,
                "networksetup": shutil.which("networksetup") is not None,
                "airport": airport_bin is not None,
                "system_profiler": shutil.which("system_profiler") is not None,
            },
            "commands": {},
            "notes": [
                "Scans run on the host where this app is running.",
                "Remote/container environments cannot see nearby laptop Wi-Fi networks.",
            ],
        }

        diagnostics["commands"]["iw_dev"] = self._run_diag_command(["iw", "dev"])
        diagnostics["commands"]["nmcli_wifi_list"] = self._run_diag_command(
            ["nmcli", "-t", "-f", "SSID,SIGNAL,DEVICE", "device", "wifi", "list"]
        )
        diagnostics["commands"]["iwconfig_interface"] = self._run_diag_command(
            ["iwconfig", interface] if interface else ["iwconfig"]
        )
        diagnostics["commands"]["iwlist_scan"] = self._run_diag_command(
            ["iwlist", interface, "scan"] if interface else ["iwlist", "scan"],
            timeout=20,
        )
        diagnostics["commands"]["networksetup_ports"] = self._run_diag_command(
            ["networksetup", "-listallhardwareports"]
        )
        diagnostics["commands"]["networksetup_current"] = self._run_diag_command(
            ["networksetup", "-getairportnetwork", interface] if interface else ["networksetup", "-getairportnetwork", "en0"]
        )
        diagnostics["commands"]["airport_scan"] = self._run_diag_command(
            [airport_bin, "-s"] if airport_bin else ["airport", "-s"],
            timeout=15,
        )
        diagnostics["commands"]["system_profiler_airport"] = self._run_diag_command(
            ["system_profiler", "SPAirPortDataType"],
            timeout=30,
        )
        return diagnostics

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_interface() -> str:
        """Return first available wireless interface."""
        if platform.system() == "Darwin":
            try:
                out = subprocess.check_output(
                    ["networksetup", "-listallhardwareports"],
                    text=True,
                    timeout=5,
                )
                chunks = out.split("\n\n")
                for chunk in chunks:
                    if "Hardware Port: Wi-Fi" in chunk:
                        m = re.search(r"Device: (\S+)", chunk)
                        if m:
                            return m.group(1)
            except Exception:
                pass
            return "en0"

        try:
            out = subprocess.check_output(["iw", "dev"], text=True, timeout=5)
            for line in out.splitlines():
                m = re.search(r"Interface\s+(\S+)", line)
                if m:
                    return m.group(1)
        except Exception:
            pass
        return "wlan0"

    def _nmcli_status(self, status: WlanStatus) -> WlanStatus:
        out = subprocess.check_output(
            ["nmcli", "-t", "-f", "ACTIVE,SSID,BSSID,SIGNAL,DEVICE", "device", "wifi"],
            text=True,
            timeout=5,
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
            ["nmcli", "-t", "-f", "SSID,BSSID,SIGNAL,CHAN,SECURITY,FREQ", "device", "wifi", "list"],
            text=True,
            timeout=10,
        )
        networks: list[WlanNetwork] = []
        for line in out.splitlines():
            parts = _split_nmcli(line)
            if len(parts) >= 6 and parts[0]:
                try:
                    networks.append(
                        WlanNetwork(
                            ssid=parts[0],
                            bssid=parts[1],
                            signal=int(parts[2]) if parts[2].isdigit() else 0,
                            channel=int(parts[3]) if parts[3].isdigit() else 0,
                            security=parts[4],
                            frequency=parts[5],
                        )
                    )
                except (ValueError, IndexError):
                    continue
        return networks

    def _iwconfig_status(self, status: WlanStatus) -> WlanStatus:
        out = subprocess.check_output(
            ["iwconfig", self.interface],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=5,
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
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=15,
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

    def _macos_status(self, status: WlanStatus) -> WlanStatus:
        """Get current macOS Wi-Fi status using airport output."""
        airport_bin = self._macos_airport_bin()
        if airport_bin:
            out = subprocess.check_output([airport_bin, "-I"], text=True, timeout=5)
            ssid_match = re.search(r"\bSSID: (.+)", out)
            bssid_match = re.search(r"\bBSSID: (.+)", out)
            rssi_match = re.search(r"\bagrCtlRSSI: (-?\d+)", out)

            if ssid_match:
                status.ssid = ssid_match.group(1).strip()
                status.connected = bool(status.ssid)
            if bssid_match:
                status.bssid = bssid_match.group(1).strip()
            if rssi_match:
                status.signal = int(rssi_match.group(1))
        else:
            # Fallback for macOS versions without the private airport binary.
            out = subprocess.check_output(
                ["networksetup", "-getairportnetwork", self.interface or "en0"],
                text=True,
                timeout=5,
                stderr=subprocess.DEVNULL,
            )
            m = re.search(r"Current Wi-Fi Network: (.+)", out)
            if m:
                status.ssid = m.group(1).strip()
                status.connected = bool(status.ssid)

        status.interface = self.interface or "en0"
        if status.connected:
            status.ip_address = self._get_ip(status.interface)
        return status

    def _macos_scan(self) -> list[WlanNetwork]:
        """Scan visible WLAN networks on macOS using airport."""
        airport_bin = self._macos_airport_bin()
        if not airport_bin:
            return self._macos_scan_system_profiler()

        out = subprocess.check_output([airport_bin, "-s"], text=True, timeout=12)
        networks: list[WlanNetwork] = []

        # Skip header line. SSID may have spaces, so parse around BSSID pattern.
        for line in out.splitlines()[1:]:
            line = line.rstrip()
            if not line:
                continue
            match = re.match(
                r"^(.*?)\s+((?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2})\s+(-?\d+)\s+(\S+)\s+\S+\s+\S+\s+(.+)$",
                line,
            )
            if not match:
                continue

            ssid = match.group(1).strip()
            bssid = match.group(2)
            signal = int(match.group(3))
            channel_raw = match.group(4)
            security = match.group(5).strip()

            channel_base = channel_raw.split(",", 1)[0]
            channel_base = channel_base.split("+", 1)[0]
            channel = int(channel_base) if channel_base.isdigit() else 0

            if ssid:
                networks.append(
                    WlanNetwork(
                        ssid=ssid,
                        bssid=bssid,
                        signal=signal,
                        channel=channel,
                        security=security,
                    )
                )
        return networks

    @staticmethod
    def _macos_airport_bin() -> Optional[str]:
        for candidate in WlanManager.AIRPORT_BINS:
            if os.path.exists(candidate) and os.access(candidate, os.X_OK):
                return candidate
        return None

    def _macos_scan_system_profiler(self) -> list[WlanNetwork]:
        """Fallback scan parser for macOS versions without airport binary."""
        out = subprocess.check_output(
            ["system_profiler", "SPAirPortDataType"],
            text=True,
            timeout=30,
        )

        networks: list[WlanNetwork] = []
        in_local_section = False
        current: Optional[WlanNetwork] = None

        def flush_current() -> None:
            nonlocal current
            if current and current.ssid:
                networks.append(current)
            current = None

        meta_keys = {
            "PHY Mode",
            "Channel",
            "Country Code",
            "Network Type",
            "Security",
            "Signal / Noise",
            "Supported Channels",
        }

        for raw in out.splitlines():
            line = raw.rstrip()

            if "Other Local Wi-Fi Networks:" in line:
                in_local_section = True
                continue

            if not in_local_section:
                continue

            # Section ends when indentation drops away from the local list.
            if line and not line.startswith("      "):
                flush_current()
                break

            m_header = re.match(r"^\s{6,}(.+):\s*$", line)
            if m_header:
                name = m_header.group(1).strip()
                if name in meta_keys:
                    # Property line of existing network.
                    if current and name == "Channel":
                        m_ch = re.search(r"(\d+)", line)
                        if m_ch:
                            current.channel = int(m_ch.group(1))
                    continue

                flush_current()
                current = WlanNetwork(ssid=name)
                continue

            if not current:
                continue

            m_sec = re.search(r"Security:\s*(.+)$", line)
            if m_sec:
                current.security = m_sec.group(1).strip()

            m_signal = re.search(r"Signal / Noise:\s*(-?\d+)", line)
            if m_signal:
                current.signal = int(m_signal.group(1))

            m_channel = re.search(r"Channel:\s*(\d+)", line)
            if m_channel:
                current.channel = int(m_channel.group(1))

        flush_current()
        return networks

    @staticmethod
    def _get_ip(interface: str) -> str:
        try:
            if platform.system() == "Darwin":
                out = subprocess.check_output(
                    ["ipconfig", "getifaddr", interface],
                    text=True,
                    timeout=5,
                    stderr=subprocess.DEVNULL,
                )
                return out.strip()

            out = subprocess.check_output(
                ["ip", "-4", "addr", "show", interface],
                text=True,
                timeout=5,
            )
            m = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", out)
            return m.group(1) if m else ""
        except Exception:
            return ""

    @staticmethod
    def _run_diag_command(cmd: list[str], timeout: int = 8) -> dict:
        """Execute command and return structured output for diagnostics."""
        binary = cmd[0] if cmd else ""
        if not binary or shutil.which(binary) is None:
            return {
                "command": " ".join(cmd),
                "available": False,
                "ok": False,
                "returncode": None,
                "stdout": "",
                "stderr": f"'{binary}' not found",
            }

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return {
                "command": " ".join(cmd),
                "available": True,
                "ok": result.returncode == 0,
                "returncode": result.returncode,
                "stdout": result.stdout.strip()[:4000],
                "stderr": result.stderr.strip()[:2000],
            }
        except Exception as exc:
            return {
                "command": " ".join(cmd),
                "available": True,
                "ok": False,
                "returncode": None,
                "stdout": "",
                "stderr": str(exc),
            }
