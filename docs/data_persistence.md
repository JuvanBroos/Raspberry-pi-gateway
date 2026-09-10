# Raspberry Pi WSN Gateway: Data Persistence (`db_logger.py`)

This document is Part 3 of the comprehensive guide to the Raspberry Pi Wireless Sensor Network (WSN) Gateway. It explains how telemetry and events are permanently recorded into a local SQLite database for historical analysis.

---

## 1. The Role of the Database Logger

The `db_logger.py` script acts as a decoupled recording service. Rather than tightly coupling the serial reading logic (in `mqtt_bridge.py`) with database insertions, the system relies on the MQTT broker. 

The logger simply subscribes to the MQTT wildcard topic `wsn/uplink/#`. Whenever the MQTT bridge decodes a serial frame and publishes it as JSON, the logger intercepts that JSON and writes it to disk. 

This pub/sub architecture means that if the database locks up or is temporarily busy, it will not block the serial reading thread from pulling new packets off the radios.

---

## 2. SQLite Database Schema

The script uses a lightweight, serverless SQLite database stored in a local file named `sensor_data.db`. Upon startup, the `setup_tables()` function ensures that three specific tables exist.

### 2.1 `sensor_readings` Table
This is the primary table containing the actual telemetry from the wireless nodes.

| Column | Data Type | Description |
| :--- | :--- | :--- |
| `id` | INTEGER PRIMARY KEY | Auto-incrementing row ID. |
| `timestamp_ms` | INTEGER | Epoch time in milliseconds when the packet was logged. |
| `node_id` | TEXT | The 16-bit hex address of the node (e.g., `0x1A4F`). |
| `protocol` | TEXT | The routing protocol used (`WiFi/ESP-NOW`, `Zigbee`, `LoRa`). |
| `rssi` | INTEGER | Received Signal Strength Indicator (dBm). |
| `batt_mv` | INTEGER | True battery voltage in millivolts. |
| `temp_c` | REAL | Temperature in Celsius. |
| `gnss_sats` | INTEGER | Number of GPS satellites locked. |
| `lat` | REAL | Latitude in decimal degrees. |
| `lon` | REAL | Longitude in decimal degrees. |
| `accel_x` | REAL | X-axis acceleration in G-forces. |
| `accel_y` | REAL | Y-axis acceleration in G-forces. |
| `accel_z` | REAL | Z-axis acceleration in G-forces. |

### 2.2 `node_status` Table
Records state changes and network joins. When a node changes protocols (e.g., falls back from Wi-Fi to Zigbee) or reboots, a status update is logged here. This is critical for auditing network reliability.

### 2.3 `node_logs` Table
Microcontrollers often send unstructured text logs (e.g., `ESP_LOGI` statements) over the wireless network for remote debugging. These raw strings are saved here with the `protocol` and `node_id` (if identifiable).

---

## 3. Data Ingestion Flow

The `on_mqtt_message` callback handles the real-time ingestion.

1. **Decoding:** It parses the incoming MQTT payload from a byte-string into a Python JSON dictionary.
2. **Topic Routing:** It checks the specific topic (`msg.topic`):
   - If `wsn/uplink/data`: It prepares an `INSERT INTO sensor_readings` SQL query.
   - If `wsn/uplink/status`: It prepares an `INSERT INTO node_status` SQL query.
   - If `wsn/uplink/log`: It prepares an `INSERT INTO node_logs` SQL query.
3. **Safe Value Extraction:** It utilizes Python's `.get("key", default)` dictionary method. This ensures that if a sensor packet is malformed or missing a specific field (e.g., a node without a GPS module omits `gnss_sats`), the script gracefully falls back to a default value (like `0` or `0.0`) without throwing a `KeyError` and crashing the logger.
4. **Transaction Commit:** `self.conn.commit()` is called immediately to persist the row to the physical disk.
