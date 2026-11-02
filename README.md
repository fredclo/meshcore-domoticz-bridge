# MeshCore ↔ Domoticz Bridge

A lightweight Python bridge that periodically polls a remote **MeshCore** repeater's telemetry (battery voltage + temperature) through a MeshCore companion radio, and publishes the values to **Domoticz** via MQTT — so Domoticz logs and plots them like any other sensor.

```
┌──────────────────────────┐       LoRa mesh       ┌──────────────────────────┐
│  Remote repeater         │  ◄──────────────────►  │  Companion (e.g. Heltec  │
│  (e.g. Faketec v3 +      │   req_telemetry        │  v4) on WiFi, IP fixed   │
│   18650 pack on a roof)  │   ◄──────────────────► │  via DHCP reservation    │
│  - battery voltage       │   JSON (Cayenne LPP)   │                          │
│  - temperature           │                        └──────────┬───────────────┘
└──────────────────────────┘                                   │ TCP:5000
                                                               │
                                              ┌────────────────▼────────────────┐
                                              │  Raspberry Pi (or any Linux)    │
                                              │  ┌──────────────────────────┐   │
                                              │  │ meshcli req_telemetry    │   │
                                              │  │ (every 3h, systemd timer)│   │
                                              │  └────────────┬─────────────┘   │
                                              │               │ JSON            │
                                              │  ┌────────────▼─────────────┐   │
                                              │  │ meshcore_to_domoticz.py  │   │
                                              │  └────────────┬─────────────┘   │
                                              │               │ MQTT            │
                                              │  ┌────────────▼─────────────┐   │
                                              │  │ Mosquitto + Domoticz     │   │
                                              │  │ Voltage + Temp curves    │   │
                                              │  └──────────────────────────┘   │
                                              └─────────────────────────────────┘
```

## Why?

MeshCore is a low-power LoRa mesh firmware with a clean companion/repeater architecture. Repeater nodes (often based on nRF52840 Pro Micro boards like the Faketec v3) already expose their battery voltage and internal MCU temperature via the `req_telemetry` MeshCore CLI command — no extra sensor needed.

**Home Assistant** already has an official MeshCore integration (`meshcore-ha`), but **Domoticz** does not. This project fills that gap with a minimal, dependency-light approach: one Python script, one systemd timer, no daemon.

## Features

- **Lightweight**: a single Python script launched every 3h by a systemd timer — no always-on daemon, no extra load on the Pi.
- **Battery-friendly polling**: the repeater only transmits when asked, instead of broadcasting telemetry continuously.
- **Zero extra hardware**: uses the repeater's native battery ADC + MCU internal temperature (no BME280/DS18B20 to wire up).
- **WiFi or USB**: companion can be reached over TCP (MeshCore v1.18+ BLE+WiFi firmware recommended — keeps BLE for the mobile app while the script polls over TCP), or over USB serial as a fallback.
- **Robust parsing**: handles the Cayenne LPP telemetry format returned by meshcli; tolerates reordering and unknown future sensor types.
- **Mobility-friendly**: when you take the companion away from home WiFi for field use, the script just logs a connection error and retries at the next 3h tick — Domoticz keeps the last value, no graph breakage.

## Requirements

### On the repeater side

- A MeshCore repeater (any supported board: Faketec v3, Heltec v3/v4, RAK WisBlock, etc.)
- Firmware recent enough to answer `req_telemetry` with a Cayenne LPP payload containing at least `voltage` and `temperature` (most nRF52-based repeater builds do).

### On the companion side

- A MeshCore companion radio (typically a Heltec v3 or v4).
- **Recommended**: MeshCore firmware **v1.18.0+**, "BLE+WiFi" build. This exposes:
  - **BLE** for the official MeshCore mobile app (use when you take the companion in the field)
  - **TCP on port 5000** for this script to poll over WiFi
- Set a **DHCP reservation** on your router so the companion's IP doesn't change.

### On the Domoticz host (Raspberry Pi or any Linux)

- Domoticz installed and running
- Mosquitto (or any MQTT broker) installed alongside Domoticz
- Python 3.10+ (the `meshcore-cli` package requires it)
- The `meshcore-cli` and `paho-mqtt` Python packages

> **Note for users of older distros (Raspberry Pi OS Buster, Debian 10, etc.)**: the system Python is 3.7, which is too old for `meshcore-cli`. See **[Troubleshooting](#troubleshooting)** below for a clean `pyenv` install of Python 3.11 alongside the system Python — no reflashing required.

## Setup

### 1. Install Mosquitto (if not already present)

```bash
sudo apt install -y mosquitto mosquitto-clients
sudo systemctl enable --now mosquitto
```

### 2. Install meshcli

The cleanest way is via `pipx` (isolated install, doesn't pollute system Python):

```bash
sudo apt install -y pipx
pipx ensurepath
# close and reopen your terminal, then:
pipx install meshcore-cli
```

Or via a venv (preferred if your system Python is old and you need pyenv — see troubleshooting):

```bash
python3 -m venv ~/meshcore-venv
source ~/meshcore-venv/bin/activate
pip install meshcore-cli paho-mqtt
```

### 3. Flash the companion with BLE+WiFi firmware

If your companion is a Heltec v4, use the official web flasher at <https://flasher.meshcore.co.uk> and select the **BLE+WiFi** variant for the Heltec v4 (requires MeshCore firmware **v1.18.0+**).

Then configure WiFi credentials on the companion via the MeshCore mobile app (Settings → WiFi Settings) or via CLI:

```bash
meshcli -j -t <companion_ip> -p 5000 set wifi.ssid "YOUR_SSID"
meshcli -j -t <companion_ip> -p 5000 set wifi.pwd "YOUR_PASSWORD"
```

Set a **DHCP reservation** for the companion on your router so its IP stays stable.

### 4. Find your repeater's contact name

List all contacts known to your companion:

```bash
meshcli -j -t 192.168.1.XX -p 5000 contacts
```

In the JSON output, find the entry matching your repeater and copy its `name` field exactly. Set it as `REPEATER_NAME` in `meshcore_to_domoticz.py`.

### 5. Test the telemetry request manually

```bash
meshcli -j -t 192.168.1.XX -p 5000 req_telemetry "MyRepeater"
```

You should get JSON like:

```json
{
    "name": "MyRepeater",
    "pubkey_pre": "xxxxxxxxxxxxxxxx",
    "lpp": [
        {"channel": 1, "type": "voltage",     "value": 4.05},
        {"channel": 1, "type": "temperature", "value": 18.2}
    ]
}
```

If `temperature` is missing, the repeater firmware doesn't expose it (or your companion's permission tier is too low — see MeshCore docs on `setperm`).

### 6. Configure Domoticz

#### 6a. Enable the MQTT Client Gateway

In Domoticz: **Setup → Hardware → Add**:
- Name: `MQTT Bridge`
- Type: **MQTT Client Gateway with MQTT interface**
- Remote Address: `127.0.0.1`
- Port: `1883`
- Topic: `domoticz/in`

#### 6b. Create virtual sensors

**Setup → Hardware → Add**:
- Name: `MeshCore Repeater`
- Type: **Dummy (Does not create devices itself)**

On that line, click **Create Virtual Sensors** (twice):
- `Tension batterie` → type **Voltage** → note the **Idx** (e.g. 100)
- `Température boîtier` → type **Temperature** → note the **Idx** (e.g. 101)

> ⚠️ **Important**: make sure the hardware line is **enabled** (not grayed out) — Domoticz silently lets you create disabled hardware, and the sensors will appear in *Devices* with values, but won't show up in the *Temperature* / *Utility* tabs. (Ask me how I know ;-))

### 7. Deploy the script

```bash
sudo mkdir -p /opt/meshcore
sudo cp meshcore_to_domoticz.py /opt/meshcore/
sudo chmod +x /opt/meshcore/meshcore_to_domoticz.py
```

Edit `/opt/meshcore/meshcore_to_domoticz.py` and set:
- `HELTEC_TCP_HOST` → your companion's IP
- `REPEATER_NAME` → the exact contact name from step 4
- `IDX_VOLTAGE`, `IDX_TEMPERATURE` → the Domoticz idx from step 6b
- `MESHCLI_BIN` → path to your meshcli binary

### 8. Install the systemd timer

```bash
sudo cp meshcore-poll.service meshcore-poll.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now meshcore-poll.timer

# Verify the timer is scheduled
systemctl list-timers meshcore-poll
```

### 9. First manual run + check

```bash
sudo systemctl start meshcore-poll.service
journalctl -u meshcore-poll.service -n 20 --no-pager
```

You should see:

```
... [INFO] Running: /home/pi/meshcore-venv/bin/meshcli -j -t 192.168.1.XX -p 5000 req_telemetry MyRepeater
... [INFO] Telemetry: {"name": "MyRepeater", "pubkey_pre": "...", "lpp": [...]}
... [INFO] Domoticz idx=100 svalue=4.05
... [INFO] Domoticz idx=101 svalue=18.2
```

And in Domoticz, both virtual sensors should update instantly. Click on a sensor name in **Setup → Devices** to open the chart — the first point appears, the curve grows with each 3h poll.

### 10. (Optional) Watch the MQTT traffic live

```bash
mosquitto_sub -t domoticz/in -v
```

You'll see each `{"idx":N,"nvalue":0,"svalue":"..."}` message as the script publishes it.

## Configuration reference

All settings are at the top of `meshcore_to_domoticz.py`:

| Constant | Default | Description |
|---|---|---|
| `TRANSPORT` | `"tcp"` | `"tcp"` (WiFi) or `"serial"` (USB) |
| `HELTEC_TCP_HOST` | `"192.168.1.XX"` | Companion's IP on your LAN |
| `HELTEC_TCP_PORT` | `5000` | MeshCore TCP companion port (default) |
| `SERIAL_PORT` | `"/dev/ttyACM0"` | Used only if `TRANSPORT="serial"` |
| `REPEATER_NAME` | `"MyRepeater"` | Exact contact name in the companion |
| `DOMOTICZ_MQTT_HOST` | `"127.0.0.1"` | MQTT broker host |
| `DOMOTICZ_MQTT_PORT` | `1883` | MQTT broker port |
| `DOMOTICZ_MQTT_TOPIC` | `"domoticz/in"` | Domoticz MQTT gateway topic |
| `IDX_VOLTAGE` | `100` | Domoticz Voltage sensor idx |
| `IDX_TEMPERATURE` | `101` | Domoticz Temperature sensor idx |
| `IDX_BATTERY_PCT` | `0` | Optional battery % idx (unused on most repeater firmware) |
| `MESHCLI_BIN` | `"/home/pi/meshcore-venv/bin/meshcli"` | Path to meshcli |
| `MESHCLI_TIMEOUT` | `120` | meshcli call timeout (seconds) |

## Adjusting the polling interval

The systemd timer fires every 3h. To change it, edit `meshcore-poll.timer`:

```ini
[Timer]
OnBootSec=2min
OnUnitActiveSec=1h    # <-- change to 1h, 30min, 6h, etc.
```

Then:

```bash
sudo systemctl daemon-reload
sudo systemctl restart meshcore-poll.timer
```

> 💡 **Battery note**: the repeater only transmits when asked. A faster poll = more mesh traffic = slightly more battery draw on the repeater. For a 18650-powered roof relay, 1h–3h is the sweet spot. Sub-15min polling is overkill.

## Troubleshooting

### `meshcli: No response from meshcore node, disconnecting`

The companion firmware probably doesn't expose USB as a companion transport. On MeshCore **v1.18.0+ BLE+WiFi** builds, USB is used only for serial logging — use TCP instead:

```bash
meshcli -j -t <companion_ip> -p 5000 infos
```

### `ERROR:meshcore:Error in parameters`

You're probably using the wrong syntax for `req_telemetry`. The contact name goes as a **direct argument**, not via `-d`:

```bash
# Wrong
meshcli -j -t <ip> -p 5000 -d "MyRepeater" req_telemetry

# Right
meshcli -j -t <ip> -p 5000 req_telemetry "MyRepeater"
```

### `meshcli req_telemetry` returns JSON without `temperature`

Either:
- The repeater firmware doesn't expose temperature (some pure-repeater builds don't). On nRF52-based boards, internal MCU temperature is usually available — reflash with a sensor-aware build if needed.
- The companion's permission tier on the repeater is too low. From the MeshCore CLI of the companion, log into the repeater and grant level 2 to the companion's public key:
  ```
  login <repeater_name> <admin_password>
  setperm <companion_pubkey> 2
  ```

### `pip install meshcore-cli` fails with "Requires Python >=3.10"

Your system Python is too old (typical on Raspberry Pi OS Buster = Python 3.7). Install Python 3.11 via `pyenv` without touching the system Python:

```bash
# Build deps
sudo apt install -y build-essential libssl-dev zlib1g-dev libbz2-dev \
  libreadline-dev libsqlite3-dev libffi-dev libncursesw5-dev \
  libgdbm-dev liblzma-dev tk-dev uuid-dev curl git

# Install pyenv (run in tmux in case your SSH drops mid-compile)
sudo apt install -y tmux
tmux new -s pyenv
curl https://pyenv.run | bash

# Add to ~/.bashrc
cat >> ~/.bashrc << 'EOF'

# pyenv
export PYENV_ROOT="$HOME/.pyenv"
[ -d "$PYENV_ROOT" ] && export PATH="$PYENV_ROOT/bin:$PATH"
eval "$("$PYENV_ROOT/bin/pyenv" init -)"
EOF
source ~/.bashrc

# Compile Python 3.11 (~25 min on Pi 3, ~10 min on Pi 4)
pyenv install 3.11.9
pyenv global 3.11.9
python3 --version    # should now show Python 3.11.9

# Create the venv with the new Python
python3 -m venv ~/meshcore-venv
source ~/meshcore-venv/bin/activate
pip install meshcore-cli paho-mqtt
```

### `python3 -m venv` fails with `ensurepip` error

On Debian/Raspbian Buster, `python3 -m venv` may fail because `ensurepip` is missing or broken. Workaround: install `python3-venv` and `python3-pip` via apt first, or create the venv with the explicit pyenv Python binary:

```bash
~/.pyenv/versions/3.11.9/bin/python3 -m venv ~/meshcore-venv
```

### Inside the venv, `python --version` shows an old version (3.7/3.8)

You almost certainly have a shell alias hijacking the `python` command. Check:

```bash
type python
# if it says "python is aliased to '/usr/bin/python3.X'", that's the culprit
unalias python
grep -rn 'alias python' ~/.bashrc ~/.bash_aliases ~/.profile /etc/profile.d/
```

Comment out the alias line in the offending file, then `source ~/.bashrc`. The script itself calls `python3` by absolute path so the alias doesn't affect it, but it interferes with manual testing.

### Sensors appear in Domoticz Devices tab but not in Temperature / Utility tabs

99% of the time: the **Dummy hardware** line is **disabled** (gray switch). Enable it, the sensors will appear in their tabs within seconds.

Other possibilities:
- Wrong **Type/SubType** when creating the virtual sensor (click the gear icon to verify).
- The sensor is marked **Hidden** (gear → uncheck "Hidden").

### Old Raspberry Pi OS Buster apt repos no longer available

```
E: The repository http://raspbian.raspberrypi.org/raspbian buster Release does not contain a Release file.
```

Edit `/etc/apt/sources.list` and replace the raspbian URL with the legacy archive:

```
deb https://legacy.raspbian.org/raspbian/ buster main contrib non-free rpi
```

Then `sudo apt update`.

## How it works

1. **systemd timer** fires `meshcore-poll.service` every 3 hours.
2. The service runs `/home/pi/meshcore-venv/bin/python3 /opt/meshcore/meshcore_to_domoticz.py`.
3. The Python script invokes `meshcli -j -t <companion_ip> -p 5000 req_telemetry <repeater_name>`.
4. `meshcli` connects to the companion (Heltec v4) over TCP, sends a telemetry request.
5. The companion forwards the request over the LoRa mesh to the repeater.
6. The repeater answers with a Cayenne LPP payload (battery voltage + temperature).
7. The companion relays the answer back to `meshcli`, which prints it as JSON.
8. The Python script parses the JSON `lpp` array, extracts `voltage` and `temperature`, and publishes them to the MQTT broker with the matching Domoticz idx.
9. Domoticz's MQTT Client Gateway receives the messages and updates the virtual sensors. The charts grow with each point.

Total cost per poll: ~5 seconds of mesh airtime (one round-trip), every 3 hours. Negligible battery impact on the repeater.

## License

MIT — see [LICENSE](LICENSE).

## Acknowledgments

- [MeshCore](https://meshcore.io) — the firmware powering the repeater and companion
- [meshcore-cli](https://github.com/meshcore-dev/meshcore-cli) — the Python CLI used to talk to the companion
- [Domoticz](https://www.domoticz.com) — the home automation system
- [Mosquitto](https://mosquitto.org) — the MQTT broker
- [paho-mqtt](https://github.com/eclipse/paho.mqtt.python) — the Python MQTT client

## See also

- [meshcore-ha](https://github.com/meshcore-dev/meshcore-ha) — official Home Assistant integration (if you're on HA, you don't need this project)
- [awesome-meshcore](https://github.com/meshcore-ita/awesome-meshcore) — curated list of MeshCore tools and integrations
