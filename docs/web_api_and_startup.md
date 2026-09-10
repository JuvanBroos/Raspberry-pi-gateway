# Raspberry Pi WSN Gateway: Web API and Startup Pipeline

This document is Part 4 of the comprehensive guide to the Raspberry Pi Wireless Sensor Network (WSN) Gateway. It details the user-facing web server (`web_api.py`) and the shell script (`start_gateway.sh`) that initializes the entire system.

---

## 1. The Web API (`web_api.py`)

The Web API serves as the frontend bridge for the gateway. It provides a modern, responsive interface for users to view live sensor data, query historical trends, and issue commands to the network. It is built using **FastAPI**, a high-performance Python web framework.

### 1.1 Architecture & Concurrency
FastAPI uses asynchronous programming (`async`/`await`). However, the MQTT client (`paho-mqtt`) operates in its own background thread. To safely bridge the multithreaded MQTT client with the asynchronous web server, the script uses `asyncio.run_coroutine_threadsafe()` to push MQTT updates into the main FastAPI event loop.

### 1.2 Live Telemetry via WebSockets
Instead of forcing the web browser to constantly poll for new data, the API pushes live updates using WebSockets.

1. **Connection:** When a user opens the dashboard, their browser connects to the `ws://<pi-ip>/ws/live` endpoint. The `ConnectionManager` accepts and stores this active WebSocket connection.
2. **Ingestion:** The script acts as an MQTT subscriber to `wsn/uplink/#`. 
3. **Broadcasting:** When a new JSON packet arrives from the MQTT Bridge, the `on_mqtt_message` callback fires. It wraps the payload, injects it into the `asyncio` event loop, and the `manager.broadcast()` function immediately pushes the JSON down every active WebSocket connection. This ensures millisecond-level latency between a sensor transmitting a packet and it appearing on the dashboard.

### 1.3 Historical Data via REST Endpoints
For fetching past data (e.g., drawing charts), the API provides standard HTTP GET endpoints that query the SQLite database (`sensor_data.db`):
* `GET /api/history?limit=100`: Returns the 100 most recent sensor readings across the entire network.
* `GET /api/history/{node_id}?limit=50`: Returns the 50 most recent readings for a specific node.

### 1.4 Command Dispatch (Downlink)
To send a command (like changing a sleep interval), the frontend makes an HTTP POST request to `/api/command` with a JSON payload matching the `CommandPayload` Pydantic schema:

```python
class CommandPayload(BaseModel):
    target_addr: str
    cmd_type: int
    proto: int
    val1: int = 0
    val2: int = 0
    val3: int = 0
```
The API validates this schema, converts it to a standard dictionary, and publishes it to the MQTT topic `wsn/downlink/command`. The `mqtt_bridge.py` script is listening to this topic and will physically construct and transmit the serial frame.

---

## 2. The Startup Pipeline (`start_gateway.sh`)

Because the gateway is composed of three separate, decoupled microservices (the MQTT Bridge, the DB Logger, and the Web API), a central initialization script is required to bring them all up cleanly.

### 2.1 Process Cleanup
The script begins by forcefully killing any lingering background processes from previous runs using `pkill -f`. This prevents port-binding errors (like "Address already in use" on port 8000 for the web API, or serial port access conflicts).

### 2.2 Serial Port Auto-Detection
Instead of hardcoding `/dev/ttyUSB0` (which can randomly swap assignments when multiple ESP32s are plugged in), the script checks for specific symlinks created by `udev` rules on the Raspberry Pi:
* `/dev/esp32c6_lora_wifi_coord`
* `/dev/esp32c6_zigbee_coord`

If these symlinks exist, it dynamically constructs the argument string (e.g., `--lora /dev/esp32c6... --wifi /dev/esp32c6...`). If they don't exist, it falls back to `/dev/ttyUSB0` and prints a warning.

### 2.3 Service Initialization
Finally, the script launches the three services:
1. `python3 mqtt_bridge.py $ARGS &` (in the background)
2. `python3 db_logger.py &` (in the background)
3. `uvicorn web_api:app --host 0.0.0.0 --port 8000` (in the foreground, binding the web server to all network interfaces so it can be accessed from any device on the local network).
