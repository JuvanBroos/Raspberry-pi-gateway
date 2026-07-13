import streamlit as st
import paho.mqtt.client as mqtt
import json
import pandas as pd
from datetime import datetime
import threading

MQTT_BROKER = "localhost"
MQTT_PORT = 1883

st.set_page_config(layout="wide", page_title="Sensor Coordinator UI")

# --- Global Thread-Safe In-Memory Storage ---
class NetworkStorage:
    lock = threading.Lock()
    nodes = {}
    logs = []

def on_connect(client, userdata, flags, rc):
    client.subscribe("gateway/node/+/data")
    client.subscribe("gateway/node/+/status")
    client.subscribe("gateway/node/+/logs")
    client.subscribe("gateway/raw_logs/+")

def on_message(client, userdata, msg):
    # --- TEMPORARY RAW DEBUG PRINT ---
    # This will dump everything hitting the broker directly into your terminal console
    print(\n"=== New MQTT Message Received ===")
    print(f"Topic: {msg.topic}")
    try:
        print(f"Payload: {msg.payload.decode()}")
    except Exception as e:
        print(f"Payload (Raw Bytes): {msg.payload}")
    # ---------------------------------

    parts = msg.topic.split('/')
    timestamp = datetime.now().strftime("%H:%M:%S")
    
    with NetworkStorage.lock:
        NetworkStorage.last_packet_time = timestamp
        
        # Capture raw logs cleanly
        if "raw_logs" in msg.topic:
            try:
                proto_id = parts[-1]
                proto_name = {"0": "WiFi", "1": "Zigbee", "2": "LoRa"}.get(proto_id, "UNK")
                NetworkStorage.logs.append(f"[{timestamp}] [{proto_name} Raw] {msg.payload.decode()}")
            except Exception:
                pass
        
        # Fallback greedy match: if any topic ends in 'data', try to capture it
        elif msg.topic.endswith("/data"):
            try:
                # Dynamically extract the node ID assuming it's the second-to-last or third element
                node_id = parts[-2] 
                data = json.loads(msg.payload.decode())
                data["Last Seen"] = timestamp
                NetworkStorage.nodes[node_id] = data
            except Exception as e:
                print(f"DEBUG: Failed to parse data packet: {e}")

        # Keep logs from spilling over
        if len(NetworkStorage.logs) > 100:
            NetworkStorage.logs.pop(0)

@st.cache_resource
def start_mqtt():
    # Explicitly using modern configuration to stop deprecation log noise
    client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION1)
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(MQTT_BROKER, MQTT_PORT, 60)
    client.loop_start()
    return client

# Spin up the listener 
mqtt_client = start_mqtt()

# --- UI Render Section ---
st.title("📡 Multi-Protocol Coordinator Cockpit")

col_left, col_right = st.columns([3, 1])

with col_left:
    st.subheader("Discovered Node Telemetry")
    
    # Read cleanly from memory with thread locking
    with NetworkStorage.lock:
        active_nodes = dict(NetworkStorage.nodes)
        active_logs = list(NetworkStorage.logs)
    
    if active_nodes:
        raw_df = pd.DataFrame.from_dict(active_nodes, orient='index')
        
        # Check if accelerometer details are grouped or independent
        if 'Accel_X' in raw_df.columns:
            raw_df['Accel X/Y/Z'] = raw_df.apply(lambda r: f"{r.get('Accel_X',0)}/{r.get('Accel_Y',0)}/{r.get('Accel_Z',0)}", axis=1)
            display_df = raw_df.reindex(columns=["Proto", "RSSI", "Batt", "Temp", "GNSS", "Lat", "Lon", "Accel X/Y/Z", "Last Seen"])
        else:
            display_df = raw_df
            
        st.dataframe(display_df, use_container_width=True)
    else:
        st.info("Scanning for incoming network telemetry matrices... Click Refresh to pull new frames.")

    st.subheader("Network Event Log Stream")
    if active_logs:
        log_block = "\n".join(reversed(active_logs))
        st.text_area("Logs Container", value=log_block, height=250, disabled=True, label_visibility="collapsed")

with col_right:
    st.subheader("Command Console")
    with st.form("downlink_form"):
        target_addr = st.text_input("Target Hex Address", placeholder="e.g. 1A4F").strip()
        cmd_type_str = st.selectbox("Action Directive", ["Change Protocol", "Change Sleep Interval", "Read Attributes", "Trigger GNSS"])
        proto_str = st.selectbox("Fallback Transceiver Route", ["WiFi/ESP-NOW", "Zigbee", "LoRa"])
        sleep_entry = st.text_input("Sleep Window (s)", value="10")
        
        submitted = st.form_submit_button("Transmit Command")
        
        if submitted:
            if not target_addr:
                st.error("Target identifier missing")
            else:
                cmd_map = {"Change Protocol": 0x02, "Change Sleep Interval": 0x03, "Read Attributes": 0x01, "Trigger GNSS": 0x04}
                proto_map = {"WiFi/ESP-NOW": 0, "Zigbee": 1, "LoRa": 2}
                
                payload = {
                    "target_addr": target_addr if target_addr.startswith("0x") else f"0x{target_addr.upper()}",
                    "cmd_type": cmd_map[cmd_type_str],
                    "fallback_proto": proto_map[proto_str],
                    "target_proto_enum": proto_map[proto_str],
                    "sleep_val": int(sleep_entry) if sleep_entry.isdigit() else 10
                }
                
                mqtt_client.publish("gateway/commands", json.dumps(payload))
                st.success(f"Downlink instruction published.")

# Explicit trigger to force a script rerun and paint new background updates
st.button("🔄 Refresh Matrices")
