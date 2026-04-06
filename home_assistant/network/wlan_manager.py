"""WLAN network manager: list available networks and show current connection."""

from __future__ import annotations

import ipaddress
import logging
import os
import platform
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Demo / mock data – returned when HA_DEMO_MODE=1 and no real hardware exists
# ---------------------------------------------------------------------------
_DEMO_NETWORKS = [
    {"ssid": "HomeNetwork_5G",    "bssid": "AA:BB:CC:DD:EE:01", "signal": 85, "channel": 36, "security": "WPA2", "frequency": "5 GHz"},
    {"ssid": "HomeNetwork_2.4G",  "bssid": "AA:BB:CC:DD:EE:02", "signal": 72, "channel": 6,  "security": "WPA2", "frequency": "2.4 GHz"},
    {"ssid": "Neighbor_WiFi",     "bssid": "AA:BB:CC:DD:EE:03", "signal": 45, "channel": 11, "security": "WPA2", "frequency": "2.4 GHz"},
    {"ssid": "GuestNetwork",      "bssid": "AA:BB:CC:DD:EE:04", "signal": 60, "channel": 1,  "security": "Open", "frequency": "2.4 GHz"},
    {"ssid": "IoT_Devices",       "bssid": "AA:BB:CC:DD:EE:05", "signal": 78, "channel": 44, "security": "WPA3", "frequency": "5 GHz"},
]

_DEMO_STATUS = {
    "connected": True,
    "ssid": "HomeNetwork_5G",
    "bssid": "AA:BB:CC:DD:EE:01",
    "ip_address": "192.168.1.42",
    "interface": "wlan0 (demo)",
    "signal": 85,
}


def _demo_mode_enabled() -> bool:
    return os.environ.get("HA_DEMO_MODE", "0") == "1"


def _inet_ntoa(mask_int: int) -> str:
    """Convert a 32-bit integer network mask to dotted-decimal notation."""
    return str(ipaddress.IPv4Address(mask_int))


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
        self._last_connect_error: str = ""

    def get_last_connect_error(self) -> str:
        """Return the latest human-readable connect error."""
        return self._last_connect_error

    def _set_connect_error(self, message: str) -> None:
        self._last_connect_error = (message or "").strip()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_status(self) -> WlanStatus:
        """Return current WLAN connection status."""
        status = WlanStatus(interface=self.interface)

        if platform.system() == "Windows":
            try:
                return self._windows_status(status)
            except Exception as exc:
                logger.debug("Windows WLAN status failed: %s", exc)
                return status

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

        # Demo mode fallback when no real hardware is available.
        if not status.connected and _demo_mode_enabled():
            d = _DEMO_STATUS
            return WlanStatus(
                connected=d["connected"],
                ssid=d["ssid"],
                bssid=d["bssid"],
                ip_address=d["ip_address"],
                interface=d["interface"],
                signal=d["signal"],
            )
        return status

    def scan_networks(self) -> list[WlanNetwork]:
        """Return a list of visible WLAN access-points."""
        networks: list[WlanNetwork] = []

        if platform.system() == "Windows":
            try:
                return self._windows_scan()
            except Exception as exc:
                logger.warning("Windows WLAN scan failed: %s", exc)
                return []

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

        # Demo mode fallback when no real hardware/tools are available.
        if not networks and _demo_mode_enabled():
            logger.info("Demo mode active: returning mock WLAN networks")
            return [
                WlanNetwork(
                    ssid=n["ssid"], bssid=n["bssid"], signal=n["signal"],
                    channel=n["channel"], security=n["security"], frequency=n["frequency"]
                )
                for n in _DEMO_NETWORKS
            ]
        return networks

    def connect(self, ssid: str, password: str = "") -> bool:
        """Connect to an SSID using OS-native WLAN tools."""
        self._set_connect_error("")
        if not ssid or len(ssid) > 32:
            self._set_connect_error("Invalid SSID (empty or too long)")
            logger.error(self._last_connect_error)
            return False
        if not all(0x20 <= ord(c) <= 0x7E for c in ssid):
            self._set_connect_error("SSID contains non-printable characters")
            logger.error(self._last_connect_error)
            return False

        if platform.system() == "Windows":
            return self._windows_connect(ssid, password)

        try:
            cmd = ["nmcli", "device", "wifi", "connect", ssid]
            if password:
                cmd += ["password", password]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode == 0:
                return True

            err = (result.stderr or result.stdout or "").strip()
            self._set_connect_error(err or f"Failed to connect to {ssid}")
            logger.error("Failed to connect to %s: %s", ssid, self._last_connect_error)
            return False
        except Exception as exc:
            self._set_connect_error(str(exc))
            logger.error("Failed to connect to %s: %s", ssid, self._last_connect_error)
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
                "wdutil": shutil.which("wdutil") is not None,
                "netsh": shutil.which("netsh") is not None,
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
        diagnostics["commands"]["wdutil_scan"] = self._run_diag_command(
            ["wdutil", "scan"],
            timeout=20,
        )
        diagnostics["commands"]["netsh_interfaces"] = self._run_diag_command(
            ["netsh", "wlan", "show", "interfaces"],
        )
        diagnostics["commands"]["netsh_networks"] = self._run_diag_command(
            ["netsh", "wlan", "show", "networks", "mode=bssid"],
            timeout=15,
        )
        return diagnostics

    def get_scan_availability(self) -> dict:
        """Return a small, non-sensitive summary of WLAN scan readiness."""
        system = platform.system()
        tools = {
            "nmcli": shutil.which("nmcli") is not None,
            "iw": shutil.which("iw") is not None,
            "iwlist": shutil.which("iwlist") is not None,
            "iwconfig": shutil.which("iwconfig") is not None,
            "airport": self._macos_airport_bin() is not None,
            "networksetup": shutil.which("networksetup") is not None,
            "wdutil": shutil.which("wdutil") is not None,
            "system_profiler": shutil.which("system_profiler") is not None,
            "netsh": shutil.which("netsh") is not None,
        }

        wireless_interfaces: list[str] = []
        if system == "Linux":
            wireless_interfaces = self._linux_wireless_interfaces()
        elif system == "Darwin":
            wireless_interfaces = [self.interface] if self.interface else []
        elif system == "Windows":
            wireless_interfaces = self._windows_wireless_interfaces()

        notes: list[str] = []
        if system == "Linux" and not wireless_interfaces:
            notes.append("No wireless interface detected on this host.")
        if system == "Linux" and not any([tools["nmcli"], tools["iw"], tools["iwlist"], tools["iwconfig"]]):
            notes.append("No Linux Wi-Fi scan tools found (nmcli/iw/iwlist/iwconfig).")
        if system == "Darwin" and not any([tools["airport"], tools["networksetup"], tools["wdutil"], tools["system_profiler"]]):
            notes.append("No macOS Wi-Fi scan tools found.")
        if system == "Windows" and not tools["netsh"]:
            notes.append("No Windows Wi-Fi scan tool found (netsh).")
        if system == "Windows" and tools["netsh"] and not wireless_interfaces:
            notes.append(
                "No Wi-Fi adapter detected by Windows. "
                "Check that Wi-Fi is switched on, Airplane mode is off, "
                "and the WLAN AutoConfig service is running "
                "(run: services.msc → WLAN AutoConfig → Start)."
            )
        demo = _demo_mode_enabled()
        if demo:
            notes.append("Demo mode is active (HA_DEMO_MODE=1) – showing mock networks.")
        elif not notes:
            notes.append("Scan environment looks available.")

        return {
            "platform": system,
            "interface": self.interface,
            "wireless_interfaces": wireless_interfaces,
            "tools": tools,
            "ready": (len(wireless_interfaces) > 0) and any(tools.values()),
            "demo": demo,
            "notes": notes,
        }

    def get_subnet(self) -> Optional[str]:
        """Return the CIDR subnet for the currently active WLAN connection.

        Returns ``None`` when not connected or when the subnet cannot be
        determined from the active interface.
        """
        status = self.get_status()
        if not status.connected:
            return None
        return self._subnet_for_interface(status.interface, status.ip_address)

    def _subnet_for_interface(self, interface: str, ip_hint: str = "") -> Optional[str]:
        """Derive the CIDR subnet from *interface* or fall back to *ip_hint*.

        Supports Linux (``ip route``), macOS (``ifconfig``) and Windows
        (``netsh interface ip show address``).  When all platform-specific
        methods fail the method falls back to a /24 subnet derived from
        *ip_hint*, if that is provided.
        """
        system = platform.system()

        if system == "Linux" and interface:
            try:
                out = subprocess.check_output(
                    ["ip", "-4", "route", "show", "dev", interface],
                    text=True,
                    timeout=5,
                )
                for line in out.splitlines():
                    line = line.strip()
                    if line.startswith("default"):
                        continue
                    m = re.match(r"(\d+\.\d+\.\d+\.\d+/\d+)", line)
                    if m:
                        return m.group(1)
            except Exception as exc:
                logger.debug("ip route for interface %s failed: %s", interface, exc)

        if system == "Darwin" and interface:
            try:
                out = subprocess.check_output(
                    ["ifconfig", interface],
                    text=True,
                    timeout=5,
                )
                m = re.search(
                    r"inet (\d+\.\d+\.\d+\.\d+)\s+netmask\s+(0x[0-9a-fA-F]+|\d+\.\d+\.\d+\.\d+)",
                    out,
                )
                if m:
                    ip = m.group(1)
                    mask_str = m.group(2)
                    if mask_str.startswith("0x"):
                        mask_int = int(mask_str, 16)
                        mask = _inet_ntoa(mask_int)
                    else:
                        mask = mask_str
                    net = ipaddress.IPv4Network(f"{ip}/{mask}", strict=False)
                    return str(net)
            except Exception as exc:
                logger.debug("ifconfig for interface %s failed: %s", interface, exc)

        if system == "Windows" and interface:
            try:
                out = subprocess.check_output(
                    ["netsh", "interface", "ip", "show", "address", f"name={interface}"],
                    text=True,
                    timeout=8,
                    encoding="utf-8",
                    errors="ignore",
                )
                ip_m = re.search(
                    r"IP\s+Address[^:]*:\s*(\d+\.\d+\.\d+\.\d+)",
                    out,
                    re.IGNORECASE,
                )
                # Prefer "Subnet Prefix: x.x.x.x/n" format; fall back to mask.
                prefix_m = re.search(
                    r"Subnet\s+Prefix[^:]*:\s*\S+\s*/(\d+)",
                    out,
                    re.IGNORECASE,
                )
                mask_m = re.search(
                    r"Subnet\s+Mask[^:]*:\s*(\d+\.\d+\.\d+\.\d+)",
                    out,
                    re.IGNORECASE,
                )
                if ip_m and (prefix_m or mask_m):
                    ip = ip_m.group(1)
                    if prefix_m:
                        net = ipaddress.IPv4Network(f"{ip}/{prefix_m.group(1)}", strict=False)
                    else:
                        net = ipaddress.IPv4Network(f"{ip}/{mask_m.group(1)}", strict=False)  # type: ignore[union-attr]
                    return str(net)
            except Exception as exc:
                logger.debug(
                    "netsh ip show address for interface %s failed: %s", interface, exc
                )

        # Last resort: derive /24 from the IP hint supplied by the caller.
        if ip_hint:
            try:
                net = ipaddress.IPv4Network(f"{ip_hint}/24", strict=False)
                return str(net)
            except Exception:
                pass

        return None

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_interface() -> str:
        """Return first available wireless interface."""
        if platform.system() == "Windows":
            interfaces = WlanManager._windows_wireless_interfaces()
            return interfaces[0] if interfaces else ""

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

        # Prefer sysfs detection so we work even when ``iw`` is missing.
        try:
            for iface in WlanManager._linux_wireless_interfaces():
                return iface
        except Exception:
            pass

        try:
            out = subprocess.check_output(["iw", "dev"], text=True, timeout=5)
            for line in out.splitlines():
                m = re.search(r"Interface\s+(\S+)", line)
                if m:
                    return m.group(1)
        except Exception:
            pass
        # No wireless interface discovered on this host/runtime.
        return ""

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

    def _windows_status(self, status: WlanStatus) -> WlanStatus:
        """Get current Wi-Fi status on Windows using netsh."""
        out = subprocess.check_output(
            ["netsh", "wlan", "show", "interfaces"],
            text=True,
            timeout=8,
            encoding="utf-8",
            errors="ignore",
        )

        current_name = ""
        state = ""
        for raw in out.splitlines():
            line = raw.strip()
            if not line or ":" not in line:
                continue
            key, value = [p.strip() for p in line.split(":", 1)]
            key_l = key.lower()

            if key_l == "name":
                current_name = value
            elif key_l == "state":
                state = value.lower()
            elif key_l == "ssid" and "bssid" not in key_l and value:
                status.ssid = value
            elif key_l == "bssid" and value:
                status.bssid = value
            elif key_l == "signal":
                m = re.search(r"(\d+)", value)
                status.signal = int(m.group(1)) if m else 0

        chosen_interface = self.interface or current_name
        status.interface = chosen_interface
        status.connected = "connected" in state and bool(status.ssid)
        if status.connected and chosen_interface:
            status.ip_address = self._get_ip(chosen_interface)
        return status

    def _windows_scan(self) -> list[WlanNetwork]:
        """Scan visible Wi-Fi networks on Windows using netsh."""
        # Check if the WLAN service / adapter is available before scanning.
        try:
            iface_out = subprocess.check_output(
                ["netsh", "wlan", "show", "interfaces"],
                text=True, timeout=8, encoding="utf-8", errors="ignore",
            )
            iface_lower = iface_out.lower()
            if "there is no wireless interface" in iface_lower or "no wireless interface" in iface_lower:
                logger.warning(
                    "Windows Wi-Fi scan: no wireless interface found. "
                    "Check that Wi-Fi is enabled and the WLAN AutoConfig service is running."
                )
                return []
            if "state" not in iface_lower:
                logger.warning(
                    "Windows Wi-Fi scan: netsh returned unexpected output – "
                    "Wi-Fi adapter may be disabled or WLAN AutoConfig service is stopped."
                )
        except Exception as exc:
            logger.warning("Windows Wi-Fi interface check failed: %s", exc)
            return []

        out = subprocess.check_output(
            ["netsh", "wlan", "show", "networks", "mode=bssid"],
            text=True,
            timeout=12,
            encoding="utf-8",
            errors="ignore",
        )
        if not out.strip() or "there is no" in out.lower():
            logger.warning(
                "Windows Wi-Fi scan: netsh returned no networks. "
                "Make sure Wi-Fi is turned on and not in Airplane mode."
            )
            return []

        logger.info("Windows Wi-Fi scan: parsing netsh output (%d lines)", len(out.splitlines()))

        networks: list[WlanNetwork] = []
        current: dict[str, str | int] = {}
        current_ssid = ""
        current_security = ""
        current_frequency = ""

        def flush_current() -> None:
            nonlocal current
            if current_ssid and current.get("bssid"):
                networks.append(
                    WlanNetwork(
                        ssid=current_ssid,
                        bssid=str(current.get("bssid", "")),
                        signal=int(current.get("signal", 0) or 0),
                        channel=int(current.get("channel", 0) or 0),
                        security=current_security,
                        frequency=str(current.get("frequency", current_frequency)),
                    )
                )
            current = {}

        for raw in out.splitlines():
            line = raw.strip()
            if not line:
                continue

            m_ssid = re.match(r"^SSID\s+\d+\s*:\s*(.*)$", line, flags=re.IGNORECASE)
            if m_ssid:
                flush_current()
                current_ssid = m_ssid.group(1).strip()
                current_security = ""
                current_frequency = ""
                continue

            m_bssid = re.match(r"^BSSID\s+\d+\s*:\s*(.+)$", line, flags=re.IGNORECASE)
            if m_bssid:
                flush_current()
                current["bssid"] = m_bssid.group(1).strip()
                continue

            m_signal = re.match(r"^Signal\s*:\s*(.+)$", line, flags=re.IGNORECASE)
            if m_signal:
                mm = re.search(r"(\d+)", m_signal.group(1))
                current["signal"] = int(mm.group(1)) if mm else 0
                continue

            m_channel = re.match(r"^Channel\s*:\s*(.+)$", line, flags=re.IGNORECASE)
            if m_channel:
                mm = re.search(r"(\d+)", m_channel.group(1))
                current["channel"] = int(mm.group(1)) if mm else 0
                continue

            m_auth = re.match(r"^Authentication\s*:\s*(.+)$", line, flags=re.IGNORECASE)
            if m_auth:
                current_security = m_auth.group(1).strip()
                continue

            m_band = re.match(r"^Band\s*:\s*(.+)$", line, flags=re.IGNORECASE)
            if m_band:
                current["frequency"] = m_band.group(1).strip()
                continue

        flush_current()
        result = self._clean_networks(networks)
        logger.info("Windows Wi-Fi scan: found %d unique networks", len(result))
        return result

    def _windows_connect(self, ssid: str, password: str = "") -> bool:
        """Connect on Windows using netsh, creating a temporary profile if needed."""
        profile_name = ssid
        if password:
            profile_xml = self._windows_profile_xml(ssid, password)
            temp_file = tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False, encoding="utf-8")
            try:
                temp_file.write(profile_xml)
                temp_file.close()
                add_result = subprocess.run(
                    ["netsh", "wlan", "add", "profile", f"filename={temp_file.name}", "user=current"],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                if add_result.returncode != 0:
                    err = (add_result.stderr or add_result.stdout or "").strip()
                    err_l = err.lower()
                    # Group policy / different user-scope profiles cannot be overwritten.
                    # In that case, continue and attempt a normal connect with existing profile.
                    if (
                        "cannot be overwritten" in err_l
                        or "different user scope" in err_l
                        or "group policy" in err_l
                    ):
                        logger.warning(
                            "Wi-Fi profile for %s cannot be overwritten; attempting connect with existing profile",
                            ssid,
                        )
                    else:
                        self._set_connect_error(err or f"Failed to add Wi-Fi profile for {ssid}")
                        logger.error("Failed to add Wi-Fi profile for %s: %s", ssid, self._last_connect_error)
                        return False
            finally:
                try:
                    os.unlink(temp_file.name)
                except Exception:
                    pass

        cmd = ["netsh", "wlan", "connect", f"name={profile_name}", f"ssid={ssid}"]
        if self.interface:
            cmd.append(f"interface={self.interface}")

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
            if result.returncode == 0:
                return True

            err = (result.stderr or result.stdout or "").strip()
            self._set_connect_error(err or f"Failed to connect to {ssid}")
            logger.error("Failed to connect to %s on Windows: %s", ssid, self._last_connect_error)
            return False
        except Exception as exc:
            self._set_connect_error(str(exc))
            logger.error("Failed to connect to %s on Windows: %s", ssid, self._last_connect_error)
            return False

    @staticmethod
    def _windows_profile_xml(ssid: str, password: str) -> str:
        """Generate a WPA2-PSK Wi-Fi profile XML for netsh import."""
        ssid_hex = ssid.encode("utf-8").hex().upper()

        def _xml_escape(value: str) -> str:
            return (
                value.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;")
                .replace("'", "&apos;")
            )

        ssid_e = _xml_escape(ssid)
        password_e = _xml_escape(password)
        return (
            "<?xml version=\"1.0\"?>\n"
            "<WLANProfile xmlns=\"http://www.microsoft.com/networking/WLAN/profile/v1\">\n"
            f"  <name>{ssid_e}</name>\n"
            "  <SSIDConfig>\n"
            "    <SSID>\n"
            f"      <hex>{ssid_hex}</hex>\n"
            f"      <name>{ssid_e}</name>\n"
            "    </SSID>\n"
            "  </SSIDConfig>\n"
            "  <connectionType>ESS</connectionType>\n"
            "  <connectionMode>auto</connectionMode>\n"
            "  <MSM>\n"
            "    <security>\n"
            "      <authEncryption>\n"
            "        <authentication>WPA2PSK</authentication>\n"
            "        <encryption>AES</encryption>\n"
            "        <useOneX>false</useOneX>\n"
            "      </authEncryption>\n"
            "      <sharedKey>\n"
            "        <keyType>passPhrase</keyType>\n"
            "        <protected>false</protected>\n"
            f"        <keyMaterial>{password_e}</keyMaterial>\n"
            "      </sharedKey>\n"
            "    </security>\n"
            "  </MSM>\n"
            "</WLANProfile>\n"
        )

    def _macos_status(self, status: WlanStatus) -> WlanStatus:
        """Get current macOS Wi-Fi status using airport output."""
        airport_bin = self._macos_airport_bin()
        airport_cmd = [airport_bin, "-I"] if airport_bin else ["airport", "-I"]
        try:
            out = subprocess.check_output(airport_cmd, text=True, timeout=5)
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
        except Exception:
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
        airport_cmd = [airport_bin, "-s"] if airport_bin else ["airport", "-s"]
        try:
            out = subprocess.check_output(airport_cmd, text=True, timeout=12)
        except Exception:
            nets = self._macos_scan_wdutil()
            if not nets:
                nets = self._macos_scan_system_profiler()
            return self._clean_networks(nets)

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
        return self._clean_networks(networks)

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

    def _macos_scan_wdutil(self) -> list[WlanNetwork]:
        """Try scanning via wdutil on newer macOS versions."""
        try:
            out = subprocess.check_output(
                ["wdutil", "scan"],
                text=True,
                timeout=20,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            return []

        networks: list[WlanNetwork] = []
        current: dict[str, str] = {}

        def flush_current() -> None:
            nonlocal current
            ssid = current.get("ssid", "").strip()
            if ssid:
                signal_raw = current.get("signal", "").strip()
                channel_raw = current.get("channel", "").strip()
                signal_match = re.search(r"-?\d+", signal_raw)
                channel_match = re.search(r"\d+", channel_raw)
                networks.append(
                    WlanNetwork(
                        ssid=ssid,
                        bssid=current.get("bssid", "").strip(),
                        signal=int(signal_match.group(0)) if signal_match else 0,
                        channel=int(channel_match.group(0)) if channel_match else 0,
                        security=current.get("security", "").strip(),
                    )
                )
            current = {}

        for raw in out.splitlines():
            line = raw.strip()
            if not line:
                flush_current()
                continue

            m = re.match(r"(?i)^(ssid|network name)\s*[:=]\s*(.+)$", line)
            if m:
                flush_current()
                current["ssid"] = m.group(2).strip()
                continue

            m = re.match(r"(?i)^bssid\s*[:=]\s*(.+)$", line)
            if m:
                current["bssid"] = m.group(1).strip()
                continue

            m = re.match(r"(?i)^(rssi|signal)\s*[:=]\s*(.+)$", line)
            if m:
                current["signal"] = m.group(2).strip()
                continue

            m = re.match(r"(?i)^channel\s*[:=]\s*(.+)$", line)
            if m:
                current["channel"] = m.group(1).strip()
                continue

            m = re.match(r"(?i)^(security|auth)\s*[:=]\s*(.+)$", line)
            if m:
                current["security"] = m.group(2).strip()
                continue

        flush_current()
        return networks

    @staticmethod
    def _clean_networks(networks: list[WlanNetwork]) -> list[WlanNetwork]:
        """Normalize discovered SSIDs and keep strongest AP per SSID."""
        # Many routers expose the same SSID from multiple radios/BSSIDs.
        # Keep only the strongest entry per SSID for a cleaner dashboard list.
        best_by_ssid: dict[str, WlanNetwork] = {}
        for net in networks:
            ssid = (net.ssid or "").strip()
            if not ssid:
                continue
            if "redacted" in ssid.lower() or ssid in {"<hidden>", "<unknown>"}:
                continue
            key = ssid.lower()
            current = best_by_ssid.get(key)
            if current is None or (net.signal or 0) > (current.signal or 0):
                best_by_ssid[key] = net

        cleaned = list(best_by_ssid.values())
        cleaned.sort(key=lambda n: (n.signal or 0), reverse=True)
        return cleaned

    @staticmethod
    def _get_ip(interface: str) -> str:
        try:
            if platform.system() == "Windows":
                out = subprocess.check_output(
                    ["ipconfig"],
                    text=True,
                    timeout=6,
                    encoding="utf-8",
                    errors="ignore",
                )
                section = ""
                for block in re.split(r"\r?\n\r?\n", out):
                    if interface.lower() in block.lower():
                        section = block
                        break
                target = section or out
                m = re.search(r"\b(\d+\.\d+\.\d+\.\d+)\b", target)
                if m and not m.group(1).startswith("127."):
                    return m.group(1)
                return ""

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

    @staticmethod
    def _linux_wireless_interfaces() -> list[str]:
        """Return Linux interfaces that expose wireless sysfs metadata."""
        interfaces: list[str] = []
        base = "/sys/class/net"
        try:
            for name in os.listdir(base):
                if os.path.isdir(os.path.join(base, name, "wireless")):
                    interfaces.append(name)
        except Exception:
            return []
        return sorted(interfaces)

    @staticmethod
    def _windows_wireless_interfaces() -> list[str]:
        """Return WLAN interface names from netsh output on Windows."""
        try:
            out = subprocess.check_output(
                ["netsh", "wlan", "show", "interfaces"],
                text=True,
                timeout=8,
                encoding="utf-8",
                errors="ignore",
            )
        except Exception:
            return []

        interfaces: list[str] = []
        for raw in out.splitlines():
            line = raw.strip()
            m = re.match(r"^Name\s*:\s*(.+)$", line, flags=re.IGNORECASE)
            if m:
                interfaces.append(m.group(1).strip())
        return interfaces
