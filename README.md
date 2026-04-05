# Home Assistant – WLAN Search & Connect

> **Identify WLAN networks and home devices in the network and then control home devices from a single view.**

## Features

- **Scan for Wi-Fi networks** on both the **2.4 GHz** and **5 GHz** frequency bands
- **Display discovered SSIDs** with band, signal strength, and security type
- **Interactive selection** – choose the network you want to connect to
- **Secure password prompt** – password input is hidden from the terminal
- **Connect via NetworkManager** (`nmcli`) with proper error handling

## Requirements

| Requirement | Details |
|-------------|---------|
| **OS** | Linux with NetworkManager |
| **Python** | 3.9 or newer |
| **System tool** | `nmcli` (included with NetworkManager) |

## Installation

```bash
# Clone the repository
git clone https://github.com/Timo1234-ai/HomeAssistantNew205042026.git
cd HomeAssistantNew205042026

# (Optional) create a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install runtime dependencies
pip install -r requirements.txt
```

## Usage

```bash
python main.py
```

Example session:

```
Scanning for Wi-Fi networks on 2.4 GHz and 5 GHz…
Found 3 network(s): 2 on 2.4 GHz, 1 on 5 GHz.

#    SSID                             Band       Signal  Security
-----------------------------------------------------------------
1    HomeNetwork                      2.4 GHz       82%  WPA2
2    OfficeWifi                       5 GHz         74%  WPA2
3    GuestNetwork                     2.4 GHz       60%  --

Enter the number of the network you want to connect to (1-3), or press Enter to skip: 1
Do you want to connect to 'HomeNetwork' (2.4 GHz)? [y/N] y
Enter password for 'HomeNetwork' (leave blank for open networks):
Connecting to 'HomeNetwork'…
Successfully connected to 'HomeNetwork'.
```

## Running the Tests

```bash
pip install pytest
pytest tests/ -v
```

## Project Structure

```
HomeAssistantNew205042026/
├── main.py            # CLI entry point
├── wlan_scanner.py    # Wi-Fi network discovery
├── wlan_connector.py  # Wi-Fi connection management
├── requirements.txt   # Python dependencies
└── tests/
    ├── test_wlan_scanner.py
    ├── test_wlan_connector.py
    └── test_main.py
```

## How It Works

1. `main.py` calls `wlan_scanner.scan_networks()` which runs:
   ```
   nmcli --terse --fields SSID,BSSID,MODE,FREQ,SIGNAL,SECURITY device wifi list --rescan yes
   ```
2. The raw output is parsed into `WlanNetwork` dataclass instances, filtered to 2.4 GHz and 5 GHz bands, deduplicated by SSID, and sorted by signal strength.
3. The user selects a network and confirms the connection.
4. `wlan_connector.connect_to_network()` runs:
   ```
   nmcli device wifi connect <SSID> password <password>
   ```
