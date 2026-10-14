# Raspberry Pi WSN Gateway

A Raspberry Pi gateway for an ESP32-based wireless sensor network. The gateway reads binary reports and text logs from one or more serial-connected coordinators, validates and decodes sensor data, publishes normalized JSON over a local MQTT broker, stores uplink events in SQLite, and serves a browser dashboard for live monitoring, history, and downlink commands.

## What it does

- Bridges serial-connected ESP32 coordinators to MQTT.
- Supports three route/protocol identifiers:
  - `0`: WiFi / ESP-NOW
  - `1`: Zigbee
  - `2`: LoRa
- Parses fixed-size binary report frames beginning with `0xAA 0x55`.
- Validates report checksums before processing frames.
- Converts raw sensor values into battery voltage, Celsius temperature, GPS coordinates, and accelerometer values.
- Forwards newline-delimited serial text as gateway log messages.
- Publishes telemetry, node status, and log events on MQTT topics.
- Persists those events in a local SQLite database.
- Provides a FastAPI dashboard with live WebSocket updates, historical charts, and command dispatch.

## Architecture

The runtime is split into three Python processes connected through an MQTT broker running on `localhost:1883`:

```text
ESP32 coordinators
        │ USB/UART serial
        ▼
  mqtt_bridge.py ───────► MQTT broker ◄────── db_logger.py
        ▲                    │                    │
        │                    │                    ▼
        │                    └────────────── sensor_data.db
        │
        └──────────── web_api.py ◄──── browser dashboard
                         │
                         ├── HTTP REST endpoints
                         └── WebSocket live stream
```

### MQTT topics

| Topic | Direction | Purpose |
| --- | --- | --- |
| `wsn/uplink/data` | coordinators → gateway | Decoded sensor telemetry |
| `wsn/uplink/status` | coordinators → gateway | Node status updates |
| `wsn/uplink/log` | coordinators → gateway | Hardware and gateway log messages |
| `wsn/downlink/command` | dashboard → gateway | Commands sent to sensor nodes |

The bridge, database logger, and web API each subscribe to the topics they need. The MQTT broker itself is not included in this repository and must be installed and running separately.

## Repository layout

```text
mqtt_bridge.py          Serial ↔ MQTT bridge and binary protocol handling
db_logger.py            MQTT subscriber that writes uplink events to SQLite
web_api.py              FastAPI application, REST API, and live WebSocket bridge
start_gateway.sh        Starts the bridge, database logger, and web server
Gateway.desktop         Linux desktop launcher for start_gateway.sh
requirements.txt        Python dependencies
sensor_data.db          Created at runtime by db_logger.py (not committed)
static/
  index.html            Dashboard markup
  app.js                Live UI, charts, history, and command form logic
  style.css             Dashboard styling
previous_versions/      Earlier Streamlit and gateway-core implementations
docs/                   Detailed protocol and component documentation
```

## Requirements

- Python 3.9+ recommended.
- A local MQTT broker listening on `localhost:1883` (for example, Mosquitto).
- One or more serial-connected ESP32 coordinators.
- Linux device paths for the coordinators, such as `/dev/ttyUSB0` or udev symlinks.

Install the Python dependencies in a virtual environment:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

The dependency list includes:

- `pyserial==3.5`
- `paho-mqtt==1.6.1`
- `fastapi`
- `uvicorn[standard]`
- `pydantic`

## Running the gateway

### Start all services

`start_gateway.sh` is the normal launcher. It expects the checkout and virtual environment at the paths currently configured in the script:

```text
/home/juvanbroos/Documents/gateway/Raspberry-pi-gateway
/home/juvanbroos/Documents/gateway/Raspberry-pi-gateway/venv
```

It stops existing gateway processes, detects these optional udev symlinks, and starts the three services:

- `/dev/esp32c6_lora_wifi_coord` for LoRa and WiFi/ESP-NOW
- `/dev/esp32c6_zigbee_coord` for Zigbee
- `/dev/ttyUSB0` as the fallback for LoRa and WiFi/ESP-NOW

Run it with:

```bash
./start_gateway.sh
```

The web server listens on all interfaces at port `8000`. Open the dashboard at:

```text
http://<raspberry-pi-ip>:8000/
```

### Start the components manually

Start the bridge with at least one protocol-specific serial port:

```bash
python3 mqtt_bridge.py --wifi /dev/ttyUSB0 --lora /dev/ttyUSB0 --zigbee /dev/ttyUSB1
```

The default serial speed is `115200`; override it with `-b` or `--baudrate`:

```bash
python3 mqtt_bridge.py --wifi /dev/ttyUSB0 --baudrate 115200
```

Start the database logger and web API in separate terminals:

```bash
python3 db_logger.py
uvicorn web_api:app --host 0.0.0.0 --port 8000
```

`db_logger.py` creates `sensor_data.db` and these tables on startup:

- `sensor_readings`
- `node_status`
- `node_logs`

## Serial protocol

The active bridge expects little-endian binary report frames using the format defined by `REPORT_STRUCT_FMT` in `mqtt_bridge.py`:

```text
<BBBBBHbI64sH
```

Frames must start with `0xAA 0x55` and end with a 16-bit checksum. Sensor payloads use:

```text
<6sHBHHBiiHHHH
```

The bridge publishes normalized telemetry with fields including:

- `node_id`
- `protocol`
- `rssi`
- `batt_mv`
- `temp_c`
- `gnss_sats`
- `lat` and `lon`
- `accel_x`, `accel_y`, and `accel_z`
- `timestamp_ms`

See [`docs/serial_protocol.md`](docs/serial_protocol.md) for the frame layout and value conversions.

## Web API

The FastAPI application serves the dashboard and exposes:

- `GET /` — dashboard HTML.
- `GET /api/history?limit=100` — latest readings across nodes.
- `GET /api/history/{node_id}?limit=50` — latest readings for one node.
- `POST /api/command` — publishes a validated downlink command to MQTT.
- `WebSocket /ws/live` — streams MQTT uplink events to connected browsers.

A command payload has this shape:

```json
{
  "target_addr": "0x1234",
  "cmd_type": 3,
  "proto": 0,
  "val1": 0,
  "val2": 0,
  "val3": 10
}
```

The dashboard supports reading attributes, changing a node's protocol, changing its sleep interval, and triggering GNSS. For protocol and command details, see [`docs/web_api_and_startup.md`](docs/web_api_and_startup.md), [`docs/mqtt_bridge.md`](docs/mqtt_bridge.md), and [`docs/frontend_dashboard.md`](docs/frontend_dashboard.md).

## Documentation

The [`docs/README.md`](docs/README.md) documentation index links to detailed guides for:

1. Serial framing and protocol conversions.
2. The MQTT bridge.
3. SQLite persistence.
4. The FastAPI and startup pipeline.
5. The browser dashboard.

## Notes

- The current implementation uses FastAPI and a static HTML/CSS/vanilla JavaScript dashboard. The older Streamlit UI is retained only under `previous_versions/`.
- The dashboard loads Chart.js and the Inter font from public CDNs, so those assets require browser network access unless replaced with local copies.
- `sensor_data.db`, Python caches, local environments, and logs are ignored by `.gitignore`.
- The startup script contains machine-specific absolute paths; update those paths before using it on another Raspberry Pi.
