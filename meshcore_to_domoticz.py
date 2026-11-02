#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
meshcore_to_domoticz.py
=======================
Bridge between a MeshCore repeater (any low-power node on your mesh, e.g. a
Faketec v3 with a Li-Ion pack on a roof) and Domoticz, via a MeshCore
companion radio (e.g. Heltec v4) connected to the same LAN as Domoticz.

The script periodically (every 3h via systemd timer) asks the companion to
query the remote repeater's telemetry using `meshcli req_telemetry`. The
companion forwards the request over the mesh, the repeater answers with a
Cayenne-LPP payload (battery voltage, temperature, etc.), and this script
parses the JSON and publishes each value to Domoticz via MQTT.

No daemon, no reprogramming of the repeater, no extra sensor needed: the
repeater already exposes its battery voltage and (on most builds) its MCU
internal temperature. This script just glues meshcli output to Domoticz
virtual sensors.

Transport: TCP over WiFi (recommended for MeshCore v1.18+ BLE+WiFi firmware
on the companion, which keeps BLE available for the mobile app while the
script polls over TCP). USB serial is also supported as a fallback.

See README.md for full setup instructions.
"""

import json
import subprocess
import sys
import logging

import paho.mqtt.client as mqtt

# =====================================================================
# CONFIGURATION - adapt to your installation
# =====================================================================

# Transport: "tcp" (WiFi) or "serial" (USB cable to companion)
TRANSPORT = "tcp"

# --- TCP mode (recommended) ---
# IP address of the companion (Heltec v4) on your LAN.
# Use a DHCP reservation on your router so it doesn't change.
HELTEC_TCP_HOST = "192.168.1.XX"      # <-- replace with your companion's IP
HELTEC_TCP_PORT = 5000                 # MeshCore TCP companion default port

# --- Serial mode (fallback, if your companion is USB-only) ---
SERIAL_PORT = "/dev/ttyACM0"

# Name of the repeater's contact in your companion's contact list.
# Find it with:  meshcli -j -t <HELTEC_TCP_HOST> -p 5000 contacts
# (look for the entry matching your repeater, copy its "name" field exactly)
REPEATER_NAME = "MyRepeater"           # <-- replace with your contact name

# MQTT broker (Mosquitto) running alongside Domoticz
DOMOTICZ_MQTT_HOST = "127.0.0.1"
DOMOTICZ_MQTT_PORT = 1883
DOMOTICZ_MQTT_TOPIC = "domoticz/in"    # default topic of Domoticz MQTT gateway

# Domoticz virtual sensor idx (see README.md for how to create them)
IDX_VOLTAGE      = 100                 # <-- replace with your Domoticz Voltage idx
IDX_TEMPERATURE  = 101                 # <-- replace with your Domoticz Temperature idx
# Optional: set to 0 if you don't have this sensor. The current MeshCore
# repeater LPP payload exposes only voltage + temperature (no battery %),
# so this stays unused until the firmware evolves.
IDX_BATTERY_PCT  = 0

# Path to the meshcli binary in your venv
MESHCLI_BIN = "/home/pi/meshcore-venv/bin/meshcli"

# Timeout for the meshcli call (the mesh can be slow: allow 2 minutes)
MESHCLI_TIMEOUT = 120

# =====================================================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
log = logging.getLogger("meshcore2dom")

# =====================================================================
# MQTT
# =====================================================================
mqttc = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="meshcore-poll")


def mqtt_connect():
    mqttc.connect(DOMOTICZ_MQTT_HOST, DOMOTICZ_MQTT_PORT, 60)
    mqttc.loop_start()


def mqtt_disconnect():
    try:
        mqttc.loop_stop()
        mqttc.disconnect()
    except Exception:
        pass


def domoticz_set(idx: int, svalue):
    payload = json.dumps({"idx": idx, "nvalue": 0, "svalue": str(svalue)})
    mqttc.publish(DOMOTICZ_MQTT_TOPIC, payload, qos=0, retain=False)
    log.info("Domoticz idx=%d svalue=%s", idx, svalue)


# =====================================================================
# meshcli call: ask the companion to query the repeater's telemetry
# =====================================================================
def fetch_telemetry():
    """Run `meshcli req_telemetry <contact>` and return the parsed JSON dict."""
    cmd = [MESHCLI_BIN, "-j"]
    if TRANSPORT == "tcp":
        cmd += ["-t", HELTEC_TCP_HOST, "-p", str(HELTEC_TCP_PORT)]
    elif TRANSPORT == "serial":
        cmd += ["-s", SERIAL_PORT]
    else:
        log.error("Unknown TRANSPORT: %r (expected 'serial' or 'tcp')", TRANSPORT)
        return None
    cmd += ["req_telemetry", REPEATER_NAME]
    log.info("Running: %s", " ".join(cmd))
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=MESHCLI_TIMEOUT
        )
    except subprocess.TimeoutExpired:
        log.error("meshcli timeout (>%ds)", MESHCLI_TIMEOUT)
        return None
    except FileNotFoundError:
        log.error("meshcli not found at %s", MESHCLI_BIN)
        return None

    if result.returncode != 0:
        log.error("meshcli rc=%d stderr=%s", result.returncode,
                  (result.stderr or "")[:500])
        return None

    # The -j output may contain extra text around the JSON; extract the
    # first complete JSON object by counting braces.
    out = result.stdout or ""
    start = out.find('{')
    if start == -1:
        log.error("No JSON in meshcli output:\n%s", out[:800])
        return None
    depth = 0
    end = start
    for i, ch in enumerate(out[start:], start=start):
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    try:
        return json.loads(out[start:end])
    except json.JSONDecodeError as e:
        log.error("Invalid JSON: %s\nExcerpt:\n%s", e, out[start:end][:800])
        return None


# =====================================================================
# Extract values from the LPP array and push to Domoticz
#
# Expected MeshCore telemetry format (Cayenne LPP):
#   {
#       "name": "...",
#       "pubkey_pre": "...",
#       "lpp": [
#           {"channel": 1, "type": "voltage",     "value": 4.05},
#           {"channel": 1, "type": "temperature", "value": 18.2}
#       ]
#   }
#
# We iterate the "lpp" array and publish each known type to the matching
# Domoticz virtual sensor. Robust to entry order and to the firmware
# adding new types later (humidity, pressure, etc.) - they will just be
# ignored without crashing.
# =====================================================================
def push_to_domoticz(t: dict):
    lpp = t.get("lpp")
    if not lpp or not isinstance(lpp, list):
        log.error("No 'lpp' array in telemetry. Full JSON: %s",
                  json.dumps(t, ensure_ascii=False))
        return
    found = {"voltage": False, "temperature": False}
    for entry in lpp:
        if not isinstance(entry, dict):
            continue
        typ = entry.get("type")
        val = entry.get("value")
        if val is None:
            continue
        try:
            val = float(val)
        except (TypeError, ValueError):
            log.warning("Non-numeric value for type=%r: %r", typ, val)
            continue
        if typ == "voltage" and val > 0:
            domoticz_set(IDX_VOLTAGE, round(val, 2))
            found["voltage"] = True
        elif typ in ("temperature", "temp"):
            domoticz_set(IDX_TEMPERATURE, round(val, 1))
            found["temperature"] = True
        elif typ in ("battery_pct", "battery_percent", "battery_level",
                     "charge", "battery"):
            if IDX_BATTERY_PCT > 0:
                domoticz_set(IDX_BATTERY_PCT, int(val))
    if not found["voltage"]:
        log.warning("No 'voltage' entry in the lpp array.")
    if not found["temperature"]:
        log.warning("No 'temperature' entry in the lpp array.")


# =====================================================================
# Entry point
# =====================================================================
def main():
    log.info("=" * 60)
    log.info("Polling MeshCore telemetry -> Domoticz")
    log.info("  Transport    : TCP/WiFi %s:%d", HELTEC_TCP_HOST, HELTEC_TCP_PORT)
    log.info("  Repeater     : %s", REPEATER_NAME)
    log.info("  Domoticz MQTT: %s:%d", DOMOTICZ_MQTT_HOST, DOMOTICZ_MQTT_PORT)
    log.info("=" * 60)
    try:
        mqtt_connect()
    except Exception as e:
        log.error("MQTT connection failed: %s", e)
        sys.exit(2)
    try:
        telemetry = fetch_telemetry()
        if not telemetry:
            log.error("No telemetry received.")
            sys.exit(1)
        log.info("Telemetry: %s", json.dumps(telemetry, ensure_ascii=False))
        push_to_domoticz(telemetry)
    finally:
        mqtt_disconnect()


if __name__ == "__main__":
    main()
