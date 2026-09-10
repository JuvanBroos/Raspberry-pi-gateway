# Raspberry Pi WSN Gateway: The MQTT Bridge (`mqtt_bridge.py`)

This document is Part 2 of the comprehensive guide to the Raspberry Pi Wireless Sensor Network (WSN) Gateway, focusing on the core routing engine: the MQTT Bridge.

---

## 1. Role of the MQTT Bridge
The `mqtt_bridge.py` script serves as the central nervous system of the Raspberry Pi gateway. It is responsible for:
1. Managing multiple simultaneous serial connections to the ESP32 coordinator(s).
2. Parsing incoming binary serial frames (uplink).
3. Publishing decoded data to the MQTT message broker as JSON.
4. Listening to the MQTT broker for JSON commands (downlink).
5. Packing JSON commands into binary serial frames and transmitting them to the ESP32.

---

## 2. Multi-Threading Serial Architecture

The bridge allows the gateway to connect to multiple hardware radios simultaneously (e.g., one ESP32 coordinator dedicated to LoRa, and another handling Zigbee and Wi-Fi).

When initialized, the script reads command-line arguments (e.g., `--wifi /dev/ttyUSB0 --zigbee /dev/ttyUSB1`) and populates a `ports_config` dictionary mapping the protocol enum to the physical port.

### The `start_serial_threads` Function
To prevent blocking, the script uses Python's `threading` library. 
For every configured protocol:
1. It opens the physical serial port (`serial.Serial`).
2. It spawns a background `daemon` thread running the `read_serial_data()` loop.
3. If multiple protocols share the same physical port (e.g., Wi-Fi and LoRa on `/dev/ttyUSB0`), it intelligently shares the `serial.Serial` object rather than crashing by trying to open the port twice.

---

## 3. Uplink Data Flow (Serial to MQTT)

Inside the `read_serial_data()` thread, the script continuously pulls bytes from the serial buffer.

### 3.1 Frame Synchronization
It scans the buffer for the magic bytes `0xAA` and `0x55`. 
If it finds them, it extracts a chunk equal to `REPORT_SIZE` (which is the size of `serial_report_t`) and passes it to `parse_binary_frame()`.

If the bytes do NOT match the magic sync bytes, the script assumes the ESP32 is printing standard human-readable text logs (e.g., `ESP_LOGI`). It searches for newline characters (`\n`), extracts the string, and publishes it to the MQTT logging topic.

### 3.2 Parsing and Publishing
Inside `parse_binary_frame()`:
1. **Checksum Validation:** It computes a simple byte-sum of the frame and compares it to the attached checksum. Corrupted frames are silently dropped.
2. **Routing by Type:** Based on the `report_type` byte, it triggers the appropriate handler.
   - `REPORT_TYPE_SENSOR_DATA` (0x01): Unpacks the nested `uplink_payload_t` struct, applies math scaling to the battery and accelerometer values (as detailed in Part 1), and publishes to `wsn/uplink/data`.
   - `REPORT_TYPE_NODE_STATUS` (0x02): Publishes to `wsn/uplink/status`.
   - `REPORT_TYPE_LOG_MESSAGE` (0x03): Publishes to `wsn/uplink/log`.

The published payload is always formatted as a clean, standardized JSON string.

---

## 4. Downlink Data Flow (MQTT to Serial)

To send commands back to the sensor nodes (like changing a sleep interval), the system utilizes an MQTT subscription. 

When the `mqtt_bridge.py` connects to the broker, it subscribes to the topic `wsn/downlink/command`. When a JSON message is published to this topic (usually by the Web API), the `on_mqtt_message` callback fires and passes the payload to the `send_command()` function.

### 4.1 Command Construction
The `send_command()` function extracts the target address, command type, and values from the JSON. 

```python
target_addr = int(payload.get("target_addr", 16))
cmd_type = int(payload.get("cmd_type", 0x02))
route_proto = int(payload.get("proto", PROTO_WIFI))
```

### 4.2 Protocol-Specific Overrides (Zigbee Sleep)
Because different wireless protocols behave differently at the MAC layer, the gateway sometimes must adjust the command logic before sending it to the coordinator. 

For example, when sending a "Change Sleep Interval" command (`0x03`), the standard behavior is to put the sleep duration into `command_value_3`. However, the Zigbee stack uses a different timer mechanism. 
The bridge intercepts this:
```python
if cmd_type == 0x03: # CHANGE_SLEEP_INTERVAL
    if route_proto == PROTO_ZIGBEE:
        command_value_1_min = 0
        command_value_2_max = command_value_3 # Move the sleep value
        command_value_3 = 1 # Force Zigbee to use the rapid reporting counter
```

### 4.3 Transmission
Finally, the script packs the integers into the `<BBBBHHHH` binary struct and writes the bytes directly to the `serial.Serial` object corresponding to the `route_proto`. The ESP32 coordinator takes over from there.
