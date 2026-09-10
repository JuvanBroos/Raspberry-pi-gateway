# Raspberry Pi WSN Gateway: Architecture & Serial Protocol

This document is Part 1 of the comprehensive guide to the Raspberry Pi Wireless Sensor Network (WSN) Gateway. It covers the high-level architecture of how data flows from the microcontrollers to the Pi, and specifically details the binary serial protocol used for communication.

---

## 1. High-Level Architecture Overview

The WSN Gateway acts as the translation layer between the low-power wireless sensor network (ESP32 microcontrollers) and the high-level application stack (Databases, Dashboards).

The data flow is bidirectional:
1. **Uplink (Sensors -> Coordinator -> Pi):** Remote sensor nodes transmit data over Wi-Fi (ESP-NOW), Zigbee, or LoRa. A central "Coordinator" ESP32 receives these wireless packets and pushes them over a USB/Serial connection to the Raspberry Pi as a continuous stream of binary frames.
2. **Downlink (Pi -> Coordinator -> Sensors):** When a user triggers a command from the dashboard (e.g., "Change Sleep Interval"), the Pi constructs a binary command frame and pushes it over the serial connection to the Coordinator ESP32, which then routes it to the specific remote node.

---

## 2. The Binary Serial Protocol

To ensure fast, reliable, and space-efficient communication over the serial link, the system uses strictly defined C-structs. The Python gateway code (`mqtt_bridge.py`) uses the built-in `struct` library to pack and unpack these binary frames.

### 2.1 Synchronization Bytes
Because serial streams are continuous, the receiver needs a way to know where a packet begins. Every valid frame in this system (both uplink and downlink) begins with two **Magic Sync Bytes**:
* `SYNC_1 = 0xAA`
* `SYNC_2 = 0x55`

If the Python script encounters these bytes in the buffer, it knows to slice out the exact size of a frame and attempt to parse it.

### 2.2 Uplink Frames (`serial_report_t`)

When the Coordinator ESP32 receives data from a sensor node, it wraps it in a `serial_report_t` frame before sending it to the Pi.

**C-Struct Definition (`wsn_coordinator/main/protocol.h`):**
```c
typedef struct __attribute__((packed)) {
    uint8_t sync1;          // 0xAA
    uint8_t sync2;          // 0x55
    uint8_t length;         // Total size of payload
    uint8_t protocol;       // 0: WiFi, 1: Zigbee, 2: LoRa
    uint8_t report_type;    // 1: Sensor Data, 2: Node Status, 3: Log Message
    uint16_t src_short_addr;// 16-bit Network Address of sender
    int8_t rssi;            // Signal strength
    uint32_t timestamp;     // Gateway uptime in ms
    uint8_t payload[64];    // The actual data (uplink_payload_t)
    uint16_t checksum;      // Simple sum of bytes
} serial_report_t;
```

**Python Parsing (`mqtt_bridge.py`):**
The python script unpacks this using the format string `<BBBBBHbI64sH`.
* `<` means Little-Endian byte order.
* `B` = unsigned char (1 byte)
* `H` = unsigned short (2 bytes)
* `b` = signed char (1 byte)
* `I` = unsigned int (4 bytes)
* `64s` = 64-byte string/array

#### The Sensor Payload (`uplink_payload_t`)
If the `report_type` is `1` (Sensor Data), the 64-byte payload actually contains a nested struct called `uplink_payload_t`.

```c
typedef struct __attribute__((packed)) {
    uint8_t  ieee_addr[6];  // 64-bit MAC address
    uint16_t short_addr;    // 16-bit Network address
    uint8_t  msg_type;      // Always 0x10
    uint16_t batt_level;    // Raw Battery ADC
    uint16_t temperature;   // Temperature (scaled by 10)
    uint8_t  gnss_sats;     // GPS Satellites
    int32_t  latitude;      // Lat * 1,000,000 
    int32_t  longitude;     // Lon * 1,000,000 
    uint16_t current_ua;    // Current draw / Tx count
    uint16_t accel_x;       // Raw X axis
    uint16_t accel_y;       // Raw Y axis
    uint16_t accel_z;       // Raw Z axis
} uplink_payload_t;
```

**Data Scaling:** 
The microcontroller sends raw or minimally scaled integers to save space. The Python bridge applies the final math:
* **Battery:** Multipled by 2 to reverse a hardware voltage divider (`batt * 2`), resulting in true millivolts.
* **Temperature:** Unscaled and offset using `round(temp / 10.0 - 273.15, 2)` to get Celsius.
* **Coordinates:** Divided by 1,000,000 to get decimal degrees.
* **Accelerometer:** Unscaled and offset using `round((val - 1650) / 330, 2)` to calculate true G-forces.

### 2.3 Downlink Frames (`serial_command_t`)

When the Pi needs to send a configuration change back down to a sensor (e.g. changing its sleep interval), it constructs a `serial_command_t` frame.

**C-Struct Definition (`wsn_coordinator/main/serial/usb_serial.h`):**
```c
typedef struct __attribute__((packed)) {
    uint8_t sync1;            // 0xAA
    uint8_t sync2;            // 0x55
    uint8_t protocol;         // Routing protocol
    uint8_t command_type;     // e.g. 0x03 for Sleep Interval
    uint16_t dest_short_addr; // Target node address
    uint16_t command_value_1_min;
    uint16_t command_value_2_max;
    union {
        uint16_t report_config_delta;
        uint16_t read_attribute_id;
        uint16_t protocol;
        uint16_t sleep_interval;
    } command_value_3;
} serial_command_t;
```

**Python Construction (`mqtt_bridge.py`):**
The script packs this 12-byte frame using the format `<BBBBHHHH` and pushes it over the serial connection. The ESP32 coordinator reads this exact struct and routes it via Zigbee, LoRa, or ESP-NOW based on the `protocol` byte.
