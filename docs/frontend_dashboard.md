# Raspberry Pi WSN Gateway: Frontend Interface (`index.html`, `app.js`, `style.css`)

This document is Part 5 of the technical guide to the Raspberry Pi Wireless Sensor Network (WSN) Gateway. It covers the client-side user interface layer located in the `static/` directory, detailing the HTML structure, CSS styling architecture, and JavaScript application logic that provide real-time network observability and control.

---

## 1. Architectural Role of the Frontend

The web frontend runs entirely client-side inside the operator's browser and interacts with the FastAPI backend (`web_api.py`) across two communication channels:
1. **Bidirectional WebSocket Stream (`/ws/live`):** Provides sub-second, push-based updates for live sensor telemetry, network status transitions, and raw diagnostic logs directly from the MQTT bus.
2. **Asynchronous REST / HTTP Endpoints:**
   * `GET /api/history?limit=100`: Used on startup to populate the active nodes table with the latest known state.
   * `GET /api/history/{node_id}?limit=30`: Fetches chronological telemetry samples for time-series charting.
   * `POST /api/command`: Dispatches downlink configuration packets into the gateway pipeline.

---

## 2. Document Structure (`index.html`)

The document layout is structured into a responsive, two-column dashboard:
* **Main Content Area (Left Column):**
  * **Header & Status Indicator:** Displays the coordinator title along with a visual connection status pill (`#ws-status` dot and `#ws-text` label).
  * **Live Telemetry Table (`#telemetry-table`):** Tabular view displaying all discovered nodes, their current routing protocol, RSSI, battery voltage, temperature, satellite count, coordinates, and timestamp.
  * **Node Inspection & Chart Panel (`#node-details`):** An expandable inspection drawer containing metric selection controls (`#chart-metric-select`) and a HTML5 `<canvas>` element rendered via Chart.js.
  * **System Log Terminal (`#log-box`):** A fixed-height, auto-scrolling log console rendering raw gateway events and hardware log statements.
* **Command Console (Right Column / Aside):**
  * Contains `#command-form`, providing input fields for the target hexadecimal node address, command type selector, protocol route, and context-dependent parameters (such as sleep interval or target radio protocol).

---

## 3. Application Logic & State Management (`app.js`)

The client application logic is implemented in pure vanilla JavaScript without external UI frameworks, keeping memory footprint and execution overhead minimal on client devices.

### 3.1 Global State
The script maintains the following client-side state:
* `nodes = {}`: Hash map indexed by `node_id` storing the latest telemetry object received for each node.
* `activeChartNodeId`: Tracks which node is currently selected for historical inspection.
* `currentChartData = []`: In-memory array containing the active time-series sliding window (capped at 30 samples).
* `mainChart`: Reference to the active Chart.js line chart instance.

### 3.2 WebSocket Lifecycle & Automatic Reconnection (`connectWS`)
The browser connects to `ws://${window.location.host}/ws/live`:
* **On Open (`ws.onopen`):** Updates the UI indicator to green (`.connected`, "Live") and logs connection success.
* **On Close (`ws.onclose`):** Immediately transitions the status indicator to red (`.disconnected`, "Disconnected"), logs the disconnection event, and schedules an automatic retry in 3 seconds using `setTimeout(connectWS, 3000)`.
* **On Message (`ws.onmessage`):** Parses the incoming JSON packet containing `topic` and `payload`, and routes it by topic:
  * `wsn/uplink/data`: Invokes `updateTable(payload.node_id, payload)`. If the incoming packet matches `activeChartNodeId`, it appends the reading to `currentChartData` (dropping the oldest point if length exceeds 30) and calls `updateChartDisplay()` to refresh the chart in real-time.
  * `wsn/uplink/status`: Appends status transition notifications (e.g., node associations) to the log terminal.
  * `wsn/uplink/log`: Appends raw node and gateway hardware debug text to the log terminal.

### 3.3 Dynamic DOM Table Updates (`updateTable`)
Rather than destroying and rebuilding the entire table on every incoming packet (which causes UI flicker and high DOM thrashing), `updateTable()` checks for an existing row by ID (`#row-${nodeId}`):
* If it does not exist, a new `<tr>` is created, assigned `row.onclick = () => showNodeDetails(nodeId)`, and appended to `<tbody>`.
* The row's `innerHTML` is updated in place with the latest formatted metric values.

### 3.4 Interactive Time-Series Visualization (Chart.js Integration)
When an operator clicks a row in the table, `showNodeDetails(nodeId)` executes:
1. Displays the `#node-details` container.
2. Automatically pre-populates the Command Center form with `target_addr = nodeId` and sets the `proto` dropdown to match the node's current active protocol (`0` for WiFi, `1` for Zigbee, `2` for LoRa).
3. Issues `GET /api/history/${nodeId}?limit=30` to retrieve recent readings from SQLite.
4. Reverses the chronological array (oldest first for left-to-right plotting) and renders the metric currently selected in `#chart-metric-select`.
5. Supports live metric switching across:
   * `temp_c` (Temperature in °C)
   * `batt_mv` (Battery in mV)
   * `rssi` (Signal strength in dBm)
   * `gnss_sats` (Locked satellite count)
   * `lat` / `lon` (GPS coordinates)
   * `accel_x` / `accel_y` / `accel_z` (Acceleration in G-forces)

### 3.5 Dynamic Downlink Command Dispatch
The command form dynamically alters visible inputs based on `#cmd_type`:
* **Type 3 (Change Sleep Interval):** Displays `#sleep-inputs` (interval in seconds).
* **Type 2 (Change Protocol):** Displays `#protocol-inputs` (dropdown for WiFi/ESP-NOW, Zigbee, LoRa).
* **Other Types (Read Attributes, Trigger GNSS):** Hides secondary parameter blocks.

On submission (`onsubmit`):
1. Captures form inputs and places the user's intended value into parameter `val3` (e.g., the sleep interval in seconds or target protocol enum), delegating all lower-level radio struct formatting to `mqtt_bridge.py`.
2. Dispatches an asynchronous `POST /api/command` with a JSON payload containing `{target_addr, cmd_type, proto, val1, val2, val3}`.
3. Logs the transmitted payload or any HTTP errors directly into `#log-box`.

### 3.6 Initial State Bootstrapping
On script initialization:
1. `initCharts()` initializes the Chart.js context with custom dark-mode colors (`#94a3b8` text, translucent gridlines).
2. `connectWS()` establishes the live telemetry stream.
3. `fetch('/api/history?limit=100')` retrieves the most recent 100 entries from the database and iterates through them to populate the table with each discovered node's last known state prior to any new live broadcasts.

---

## 4. Visual Design & Styling System (`style.css`)

The visual presentation implements a high-contrast dark-mode interface designed for long-duration laboratory or field monitoring.

### 4.1 Design Tokens & CSS Variables
The stylesheet centralizes thematic constants at the root:
```css
:root {
    --bg-color: #0b0f19;
    --panel-bg: rgba(20, 25, 40, 0.6);
    --panel-border: rgba(255, 255, 255, 0.1);
    --text-main: #e2e8f0;
    --text-muted: #94a3b8;
    --accent: #38bdf8;
    --accent-glow: rgba(56, 189, 248, 0.5);
    --success: #10b981;
    --error: #ef4444;
}
```

### 4.2 Glassmorphism & Layout Engine
* **CSS Grid (`.main-layout`):** Employs a strict two-column layout (`1fr 350px`) spanning full viewport height (`calc(100vh - 40px)`). The main content expands fluidly, while the command aside remains fixed at 350px.
* **Glassmorphism:** Panels use `backdrop-filter: blur(12px)` combined with translucent RGBA backgrounds and subtle white borders (`rgba(255, 255, 255, 0.1)`), providing visual depth over the radial gradient background.
* **Sticky Table Headers:** Telemetry table headers (`<th>`) use `position: sticky; top: 0;` with a blurred background so column headings remain visible when scrolling through large node lists.
* **Console-Style Terminal (`.log-box`):** Uses monospace typography (`Courier New`), high contrast accent timestamps (`var(--accent)`), and `overflow-y: auto` to simulate a hardware serial terminal.
* **Tactile Button Cues (`.btn-glow`):** Action buttons feature glowing box-shadow transitions (`box-shadow: 0 0 15px var(--accent-glow)`) on hover to provide clear visual feedback during command dispatch.
