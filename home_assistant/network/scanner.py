"""Network device scanner.

Discovers devices on the local network using:
  1. ARP scan (via scapy or arp-scan command)
  2. mDNS/Bonjour service discovery (via zeroconf)
  3. Common smart-home port probes
"""

from __future__ import annotations

import ipaddress
import logging
import platform
import re
import socket
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# Common smart-home service ports → (service_name, device_hint)
SMART_HOME_PORTS: dict[int, tuple[str, str]] = {
    80:   ("http",       "Generic HTTP device"),
    443:  ("https",      "Generic HTTPS device"),
    554:  ("rtsp",       "IP Camera"),
    1400: ("sonos",      "Sonos Speaker"),
    1883: ("mqtt",       "MQTT Broker"),
    5353: ("mdns",       "mDNS device"),
    8080: ("http-alt",   "HTTP Alt device"),
    8123: ("hass",       "Home Assistant"),
    8883: ("mqtts",      "Secure MQTT"),
    9000: ("tcp",        "Generic TCP device"),
    49153:("upnp",       "UPnP device"),
    56700:("lifx",       "LIFX Light"),
}

# mDNS service types → friendly name
MDNS_SERVICE_MAP: dict[str, str] = {
    "_hap._tcp.local.":       "HomeKit device",
    "_http._tcp.local.":      "HTTP device",
    "_googlecast._tcp.local.": "Google Cast device",
    "_sonos._tcp.local.":     "Sonos Speaker",
    "_hue._tcp.local.":       "Philips Hue Bridge",
    "_printer._tcp.local.":   "Printer",
    "_ipp._tcp.local.":       "IPP Printer",
    "_airplay._tcp.local.":   "AirPlay device",
    "_raop._tcp.local.":      "AirPlay Audio",
    "_smb._tcp.local.":       "SMB File Server",
    "_ssh._tcp.local.":       "SSH Server",
    "_mqtt._tcp.local.":      "MQTT Broker",
    "_lifx._tcp.local.":      "LIFX Light",
}


@dataclass
class DiscoveredDevice:
    """A device found on the network."""
    ip: str
    mac: str = ""
    hostname: str = ""
    vendor: str = ""
    open_ports: list[int] = field(default_factory=list)
    services: list[str] = field(default_factory=list)
    device_type: str = "Unknown"
    plugin_id: str = ""
    last_seen: float = field(default_factory=time.time)
    identification_confidence: int = 0
    identification_sources: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "ip": self.ip,
            "mac": self.mac,
            "hostname": self.hostname,
            "vendor": self.vendor,
            "open_ports": self.open_ports,
            "services": self.services,
            "device_type": self.device_type,
            "plugin_id": self.plugin_id,
            "last_seen": self.last_seen,
            "identification_confidence": self.identification_confidence,
            "identification_sources": self.identification_sources,
        }


class DeviceScanner:
    """Scans the local network and identifies home electronic devices."""

    def __init__(self, timeout: float = 1.0) -> None:
        self.timeout = timeout
        self._devices: dict[str, DiscoveredDevice] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def scan(self, network: Optional[str] = None) -> list[DiscoveredDevice]:
        """Run a full scan and return discovered devices.

        Args:
            network: CIDR subnet to scan, e.g. ``"192.168.1.0/24"``.
                     Auto-detected if not provided.
        """
        if network is None:
            network = self._detect_network()

        logger.info("Scanning network %s …", network)
        hosts = self._arp_scan(network)
        if not hosts:
            hosts = self._ping_sweep(network)

        devices = []
        threads = []
        for ip, mac in hosts.items():
            dev = DiscoveredDevice(ip=ip, mac=mac)
            dev.hostname = self._resolve_hostname(ip)
            dev.vendor = lookup_vendor(mac)
            t = threading.Thread(
                target=self._probe_device, args=(dev,), daemon=True
            )
            threads.append((t, dev))
            t.start()

        for t, dev in threads:
            t.join(timeout=self.timeout + 2)
            self._classify(dev)
            devices.append(dev)

        with self._lock:
            for dev in devices:
                self._devices[dev.ip] = dev

        # Also run mDNS discovery asynchronously
        self._mdns_merge(devices)

        logger.info("Found %d device(s)", len(devices))
        return devices

    def get_cached(self) -> list[DiscoveredDevice]:
        """Return previously discovered devices."""
        with self._lock:
            return list(self._devices.values())

    # ------------------------------------------------------------------
    # ARP / ping sweep
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_network() -> str:
        """Detect the local subnet from host networking information."""
        system = platform.system()

        if system == "Linux":
            try:
                out = subprocess.check_output(["ip", "route"], text=True, timeout=5)
                for line in out.splitlines():
                    if "src" in line and not line.startswith("default"):
                        parts = line.split()
                        if "/" in parts[0]:
                            return parts[0]
            except Exception:
                pass

        if system == "Windows":
            try:
                out = subprocess.check_output(["ipconfig"], text=True, timeout=8)
                network = DeviceScanner._network_from_ipconfig(out)
                if network:
                    return network
            except Exception:
                pass

        # Cross-platform fallback: derive subnet from the active local IPv4.
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                s.connect(("8.8.8.8", 80))
                local_ip = s.getsockname()[0]
            finally:
                s.close()

            if local_ip and not local_ip.startswith("127."):
                net = ipaddress.IPv4Network(f"{local_ip}/24", strict=False)
                return f"{net.network_address}/{net.prefixlen}"
        except Exception:
            pass

        return "192.168.1.0/24"

    @staticmethod
    def _network_from_ipconfig(output: str) -> Optional[str]:
        """Extract active IPv4 network from Windows ipconfig output."""
        blocks = re.split(r"\r?\n\r?\n+", output)
        for block in blocks:
            lines = [line.strip() for line in block.splitlines() if line.strip()]
            if not lines:
                continue

            # Pick a plausible host IPv4 from this adapter block.
            ip_match = re.search(
                r"\b(10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[0-1])\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3})\b",
                block,
            )
            if not ip_match:
                continue
            host_ip = ip_match.group(1)
            if host_ip.startswith("169.254."):
                continue

            mask_match = re.search(r"\b(255(?:\.\d{1,3}){3})\b", block)
            if mask_match:
                try:
                    net = ipaddress.IPv4Network(f"{host_ip}/{mask_match.group(1)}", strict=False)
                    return f"{net.network_address}/{net.prefixlen}"
                except Exception:
                    continue

            # If mask is unavailable in this locale/output, assume /24.
            try:
                net = ipaddress.IPv4Network(f"{host_ip}/24", strict=False)
                return f"{net.network_address}/{net.prefixlen}"
            except Exception:
                continue
        return None

    @staticmethod
    def _arp_scan(network: str) -> dict[str, str]:
        """Return {ip: mac} using ARP table and platform-specific fallbacks."""
        hosts: dict[str, str] = {}
        try:
            target_net = ipaddress.ip_network(network, strict=False)
        except ValueError:
            target_net = None

        def _is_valid_host_ip(ip: str) -> bool:
            try:
                addr = ipaddress.ip_address(ip)
            except ValueError:
                return False
            if not isinstance(addr, ipaddress.IPv4Address):
                return False
            if addr.is_multicast or addr.is_unspecified or addr.is_loopback:
                return False
            if target_net is not None:
                if addr not in target_net:
                    return False
                if addr == target_net.network_address or addr == target_net.broadcast_address:
                    return False
            return True

        # Try scapy first
        try:
            from scapy.all import ARP, Ether, srp  # type: ignore
            packet = Ether(dst="ff:ff:ff:ff:ff:ff") / ARP(pdst=network)
            answered, _ = srp(packet, timeout=2, verbose=False)
            for _, rcv in answered:
                if _is_valid_host_ip(rcv.psrc):
                    hosts[rcv.psrc] = rcv.hwsrc
            if hosts:
                return hosts
        except Exception as exc:
            logger.debug("scapy ARP scan failed: %s", exc)

        # Fallback: arp-scan command
        try:
            out = subprocess.check_output(
                ["arp-scan", "--localnet", "--quiet"],
                text=True, stderr=subprocess.DEVNULL, timeout=20
            )
            for line in out.splitlines():
                m = re.match(
                    r"(\d+\.\d+\.\d+\.\d+)\s+([\da-fA-F:]{17})", line
                )
                if m:
                    ip = m.group(1)
                    if _is_valid_host_ip(ip):
                        hosts[ip] = m.group(2)
            if hosts:
                return hosts
        except Exception as exc:
            logger.debug("arp-scan failed: %s", exc)

        # Windows fallback: parse `arp -a` table.
        if platform.system() == "Windows":
            try:
                out = subprocess.check_output(
                    ["arp", "-a"],
                    text=True,
                    timeout=8,
                    encoding="utf-8",
                    errors="ignore",
                )
                for line in out.splitlines():
                    m = re.search(r"(\d+\.\d+\.\d+\.\d+)\s+([\da-fA-F\-]{17})\s+", line)
                    if not m:
                        continue
                    ip = m.group(1)
                    mac = m.group(2).replace("-", ":").lower()
                    if _is_valid_host_ip(ip):
                        hosts[ip] = mac
                if hosts:
                    return hosts
            except Exception as exc:
                logger.debug("arp -a parse failed: %s", exc)

        # Fallback: read /proc/net/arp (only shows already-contacted hosts)
        try:
            with open("/proc/net/arp") as fh:
                for line in fh.readlines()[1:]:
                    parts = line.split()
                    if len(parts) >= 4 and parts[2] != "0x0":
                        if _is_valid_host_ip(parts[0]):
                            hosts[parts[0]] = parts[3]
        except Exception as exc:
            logger.debug("/proc/net/arp read failed: %s", exc)

        return hosts

    @staticmethod
    def _ping_sweep(network: str) -> dict[str, str]:
        """Ping every host in the subnet and return {ip: ''} for alive ones."""
        hosts: dict[str, str] = {}
        try:
            net = ipaddress.IPv4Network(network, strict=False)
        except ValueError:
            return hosts

        is_windows = platform.system() == "Windows"

        threads = []

        def _ping(ip: str) -> None:
            try:
                cmd = ["ping", "-c", "1", "-W", "1", ip]
                if is_windows:
                    cmd = ["ping", "-n", "1", "-w", "1000", ip]
                result = subprocess.run(
                    cmd,
                    capture_output=True, timeout=3
                )
                if result.returncode == 0:
                    hosts[ip] = ""
            except Exception:
                pass

        for addr in net.hosts():
            t = threading.Thread(target=_ping, args=(str(addr),), daemon=True)
            threads.append(t)
            t.start()
            if len(threads) % 50 == 0:
                for t2 in threads[-50:]:
                    t2.join(timeout=3)

        for t in threads:
            t.join(timeout=3)
        return hosts

    # ------------------------------------------------------------------
    # Port probing
    # ------------------------------------------------------------------

    def _probe_device(self, dev: DiscoveredDevice) -> None:
        """Probe common smart-home ports on a device."""
        for port, (svc, _) in SMART_HOME_PORTS.items():
            try:
                with socket.create_connection(
                    (dev.ip, port), timeout=self.timeout
                ):
                    dev.open_ports.append(port)
                    dev.services.append(svc)
            except OSError:
                pass

    # ------------------------------------------------------------------
    # Classification
    # ------------------------------------------------------------------

    def _classify(self, dev: DiscoveredDevice) -> None:
        """Assign device_type, plugin_id, confidence and sources based on signals."""
        vendor_lower = dev.vendor.lower()
        ports = set(dev.open_ports)
        sources: list[str] = []
        confidence = 0

        def _add(src: str, pts: int) -> None:
            nonlocal confidence
            sources.append(src)
            confidence = min(100, confidence + pts)

        if 56700 in ports or "lifx" in vendor_lower:
            dev.device_type = "LIFX Light"
            dev.plugin_id = "lifx"
            if 56700 in ports:
                _add("port:56700", 40)
            if "lifx" in vendor_lower:
                _add("vendor:lifx", 30)
        elif 1400 in ports or "sonos" in vendor_lower:
            dev.device_type = "Sonos Speaker"
            dev.plugin_id = "sonos"
            if 1400 in ports:
                _add("port:1400", 40)
            if "sonos" in vendor_lower:
                _add("vendor:sonos", 30)
        elif "philips" in vendor_lower or (
            80 in ports and "hue" in dev.hostname.lower()
        ):
            dev.device_type = "Philips Hue Bridge"
            dev.plugin_id = "philips_hue"
            if "philips" in vendor_lower:
                _add("vendor:philips", 30)
            if 80 in ports and "hue" in dev.hostname.lower():
                _add("port:80", 20)
                _add("hostname:hue", 20)
        elif 554 in ports:
            dev.device_type = "IP Camera"
            dev.plugin_id = "generic_camera"
            _add("port:554", 40)
        elif 1883 in ports or 8883 in ports:
            dev.device_type = "MQTT Broker"
            dev.plugin_id = "mqtt"
            if 1883 in ports:
                _add("port:1883", 40)
            if 8883 in ports:
                _add("port:8883", 40)
        elif 8123 in ports:
            dev.device_type = "Home Assistant"
            dev.plugin_id = "home_assistant"
            _add("port:8123", 40)
        elif 80 in ports or 8080 in ports:
            dev.device_type = "Smart HTTP Device"
            dev.plugin_id = "generic_http"
            if 80 in ports:
                _add("port:80", 30)
            if 8080 in ports:
                _add("port:8080", 30)
        elif dev.vendor:
            dev.device_type = f"Device ({dev.vendor})"
            dev.plugin_id = "generic"
            _add("vendor:arp", 20)
        else:
            dev.device_type = "Unknown Device"
            dev.plugin_id = "generic"
            if dev.mac:
                _add("arp:mac", 10)
            else:
                _add("ping", 10)

        dev.identification_confidence = confidence
        dev.identification_sources = sources

    # ------------------------------------------------------------------
    # mDNS
    # ------------------------------------------------------------------

    def _mdns_merge(self, devices: list[DiscoveredDevice]) -> None:
        """Try zeroconf mDNS discovery and merge into device list."""
        try:
            mdns_results = self._zeroconf_scan()
            ip_map = {d.ip: d for d in devices}
            for ip, info in mdns_results.items():
                svc_type = info.get("service_type", "mdns")
                if ip in ip_map:
                    dev = ip_map[ip]
                    if info.get("service_type"):
                        dev.services.append(svc_type)
                    if info.get("device_type"):
                        dev.device_type = info["device_type"]
                        dev.plugin_id = info.get("plugin_id", dev.plugin_id)
                    mdns_src = f"mdns:{svc_type}"
                    if mdns_src not in dev.identification_sources:
                        dev.identification_sources.append(mdns_src)
                    dev.identification_confidence = min(
                        100, dev.identification_confidence + 30
                    )
                else:
                    # Device only visible via mDNS
                    dev = DiscoveredDevice(ip=ip)
                    dev.hostname = info.get("name", "")
                    dev.device_type = info.get("device_type", "mDNS Device")
                    dev.plugin_id = info.get("plugin_id", "generic")
                    dev.services = [svc_type]
                    dev.identification_sources = [f"mdns:{svc_type}"]
                    dev.identification_confidence = 50
                    with self._lock:
                        self._devices[ip] = dev
                    devices.append(dev)
        except Exception as exc:
            logger.debug("mDNS discovery failed: %s", exc)

    @staticmethod
    def _zeroconf_scan(timeout: float = 3.0) -> dict[str, dict]:
        """Perform mDNS browsing via zeroconf library."""
        results: dict[str, dict] = {}
        try:
            from zeroconf import ServiceBrowser, Zeroconf  # type: ignore

            class _Listener:
                def add_service(self, zc: Zeroconf, svc_type: str, name: str) -> None:
                    info = zc.get_service_info(svc_type, name)
                    if info and info.addresses:
                        import socket as _socket
                        ip = _socket.inet_ntoa(info.addresses[0])
                        friendly = MDNS_SERVICE_MAP.get(svc_type, svc_type)
                        plugin_id = _service_to_plugin(svc_type)
                        results[ip] = {
                            "name": name,
                            "service_type": svc_type,
                            "device_type": friendly,
                            "plugin_id": plugin_id,
                        }

                def remove_service(self, *_: object) -> None:
                    pass

                def update_service(self, *_: object) -> None:
                    pass

            zc = Zeroconf()
            browsers = [
                ServiceBrowser(zc, svc, _Listener())
                for svc in MDNS_SERVICE_MAP
            ]
            time.sleep(timeout)
            zc.close()
            _ = browsers  # keep references alive
        except Exception as exc:
            logger.debug("zeroconf browse failed: %s", exc)
        return results

    # ------------------------------------------------------------------
    # Misc
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_hostname(ip: str) -> str:
        try:
            return socket.gethostbyaddr(ip)[0]
        except Exception:
            return ""


def _service_to_plugin(svc_type: str) -> str:
    service_plugin_map = {
        "_hap._tcp.local.":        "homekit",
        "_googlecast._tcp.local.": "chromecast",
        "_sonos._tcp.local.":      "sonos",
        "_hue._tcp.local.":        "philips_hue",
        "_lifx._tcp.local.":       "lifx",
        "_mqtt._tcp.local.":       "mqtt",
    }
    return service_plugin_map.get(svc_type, "generic")


# ---------------------------------------------------------------------------
# MAC vendor lookup
# ---------------------------------------------------------------------------

_OUI_CACHE: dict[str, str] = {}

def lookup_vendor(mac: str) -> str:
    """Return the vendor name for a MAC address using an OUI cache or API."""
    if not mac or mac in ("", "00:00:00:00:00:00"):
        return ""
    oui = mac.replace("-", ":").upper()[:8]
    if oui in _OUI_CACHE:
        return _OUI_CACHE[oui]

    # Try local ARP-scan / ieee OUI file
    try:
        with open("/usr/share/arp-scan/ieee-oui.txt") as fh:
            for line in fh:
                if line.upper().startswith(oui.replace(":", "")):
                    vendor = line.split("\t", 2)[-1].strip()
                    _OUI_CACHE[oui] = vendor
                    return vendor
    except Exception:
        pass

    # Fallback: macaddress library
    try:
        import macaddress  # type: ignore
        vendor = str(macaddress.EUI48(mac).org)
        _OUI_CACHE[oui] = vendor
        return vendor
    except Exception:
        pass

    return ""
