# HomeAssistantNew205042026

A local home assistant application that connects to your WLAN, automatically discovers smart-home devices on the network, retrieves device-specific API integrations, and lets you control everything from a single web dashboard.

![Home Assistant Dashboard](https://github.com/user-attachments/assets/d7802b09-09da-49a9-b7ea-af22ec4e76d6)

---

## Features

| Feature | Description |
|---------|-------------|
| **WLAN management** | List nearby networks, see current connection status, connect to a network |
| **Network scan** | ARP scan + mDNS/Bonjour discovery to find every device on your subnet |
| **Device identification** | Port fingerprinting + MAC-vendor lookup automatically classifies devices |
| **Plugin system** | Built-in plugins for Philips Hue, Sonos, LIFX, Chromecast, MQTT, generic HTTP devices |
| **Remote plugin registry** | Fetches additional community plugins (TP-Link Kasa, WLED, Shelly…) from the internet at runtime |
| **Single-view dashboard** | Dark-themed Bootstrap 5 UI; click any device card to view state and run commands |

---

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Run the application

```bash
python app.py
```

Open your browser at `http://localhost:5000`.

### 3. Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `HA_HOST` | `0.0.0.0` | Bind address |
| `HA_PORT` | `5000` | Port |
| `HA_DEBUG` | `0` | Enable Flask debug mode (`1` = on) |
| `HA_PLUGIN_REGISTRY_URL` | (GitHub raw URL) | Override the remote plugin registry URL |

---

## Project Structure

```
.
├── app.py                          # Flask application factory & entry point
├── requirements.txt
├── plugin_registry.json            # Remote plugin registry (hosted on GitHub)
├── home_assistant/
│   ├── network/
│   │   ├── wlan_manager.py         # WLAN status, scan & connect (nmcli / iwconfig)
│   │   └── scanner.py              # ARP scan, mDNS discovery, device classification
│   ├── devices/
│   │   ├── plugin_manager.py       # Plugin loader (built-in + remote download)
│   │   └── plugins/
│   │       ├── philips_hue.py
│   │       ├── sonos.py
│   │       ├── lifx.py
│   │       ├── chromecast.py
│   │       ├── mqtt_device.py
│   │       ├── generic_http.py
│   │       └── generic.py
│   └── api/
│       └── routes.py               # Flask Blueprint – REST API
├── templates/
│   └── index.html                  # Single-page dashboard
├── static/
│   ├── css/dashboard.css
│   └── js/dashboard.js
├── community_plugins/              # Example community plugins
│   ├── tplink_kasa.py
│   ├── wled.py
│   └── shelly.py
└── tests/
    ├── test_api_routes.py
    ├── test_plugin_manager.py
    ├── test_scanner.py
    └── test_wlan_manager.py
```

---

## REST API

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/wlan/status` | Current WLAN connection |
| GET | `/api/wlan/networks` | Available networks |
| POST | `/api/wlan/connect` | Connect `{ssid, password}` |
| GET | `/api/devices/scan` | Scan network (optional `?network=x.x.x.x/24`) |
| GET | `/api/devices` | Cached device list |
| GET | `/api/devices/<ip>/state` | Device current state |
| GET | `/api/devices/<ip>/capabilities` | Supported commands |
| POST | `/api/devices/<ip>/command` | Execute `{command, params}` |
| GET | `/api/plugins` | All available plugins |
| POST | `/api/plugins/refresh` | Force-refresh remote registry |

---

## Adding a Community Plugin

Create a Python file that subclasses `DevicePlugin`, host it publicly (e.g. GitHub raw), and add an entry to `plugin_registry.json`:

```json
{
  "my_device": {
    "name": "My Smart Device",
    "description": "Controls My Smart Device via local API",
    "version": "1.0.0",
    "url": "https://raw.githubusercontent.com/you/repo/main/my_device.py"
  }
}
```

The application downloads and hot-loads the plugin on first use.

---

## Running Tests

```bash
python -m pytest tests/ -v
```

60 tests covering WLAN manager, network scanner, plugin manager, and all API routes.
 
