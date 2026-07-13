import streamlit as st
import paho.mqtt.client as mqtt
import json
import pandas as pd
from datetime import datetime

MQTT_BROKER = "localhost"
MQTT_PORT = 1883

st.set_page_config(layout="wide", page_title="Sensor Coordinator UI")

# --- Global Thread-Safe In-Memory Stores ---
# These sit completely outside Streamlit's session state to avoid cross-thread crashes
if "GLOBAL_NODES" not in st.annotations:
    st.annotations["GLOBAL_NODES"] = {}
if "GLOBAL_LOGS" not in st.annotations:
    st.annotations["GLOBAL_LOGS"] = []

def on_connect(client, userdata, flags, rc):
    client.subscribe("gateway/node/+/data")
    client.subscribe("gateway/node/+/status")
    client.subscribe("gateway/node/+/logs")
    client.subscribe("gateway/raw_logs/+")

def on_message(client, userdata, msg):
    parts = msg.topic.split('/')
    timestamp = datetime.now().strftime("%H:%M:%S")
    
    if "raw_logs" in msg.topic:
        proto_id = parts[2]
        proto_name = {"0": "WiFi", "1": "Zigbee", "2": "LoRa"}.get(proto_id, "UNK")
        st.annotations["GLOBAL_LOGS"].append(f"[{timestamp}] [{proto_name} Raw] {msg.payload.decode()}")
    else:
        node_id = parts[2]
        sub_type = parts[3]
        
        if sub_type == "data":
            try:
                data = json.loads(msg.payload.decode())
                data["Last Seen"] = timestamp
                st.annotations["GLOBAL_NODES"][node_id] = data
            except Exception as e:
                pass
        elif sub_type == "status":
            st.annotations["GLOBAL_LOGS"].append(f"[{timestamp}] [{node_id}] Node Status Frame Received")
        elif sub_type == "logs":
            st.annotations["GLOBAL_LOGS"].append(f"[{timestamp}] [{node_id}] {msg.payload.decode()}")

    # Cap log size
    if len(st.annotations["GLOBAL_LOGS"]) > 100:
        st.annotations["GLOBAL_LOGS"].pop(0)

@st.cache_resource
def start_mqtt():
    # Use explicit modern API flag to prevent deprecation prints
    client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION1)
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(MQTT_BROKER, MQTT_PORT, 60)
    client.loop_start()
    return client

# Start the background listener loop safely
mqtt_client = start_mqtt()

# --- UI Render Section ---
st.title("📡 Multi-Protocol Coordinator Cockpit")

col_left, col_right = st.columns([3, 1])

with col_left:
    st.subheader("Discovered Node Telemetry")
    
    # Read directly from the thread-safe global memory store
    active_nodes = st.annotations["GLOBAL_NODES"]
    
    if active_nodes:
        raw_df = pd.DataFrame.from_dict(active_nodes, orient='index')
        
        if 'Accel_X' in raw_df.columns:
            raw_df['Accel X/Y/Z'] = raw_df.apply(lambda r: f"{r.get('Accel_X',0)}/{r.get('Accel_Y',0)}/{r.get('Accel_Z',0)}", axis=1)
            display_df = raw_df.reindex(columns=["Proto", "RSSI", "Batt", "Temp", "GNSS", "Lat", "Lon", "Accel X/Y/Z", "Last Seen"])
        else:
            display_df = raw_df
            
        st.dataframe(display_df, use_container_width=True)
    else:
        st.info("Scanning for incoming network telemetry matrices... Click Refresh to pull new frames.")

    st.subheader("Network Event Log Stream")
    active_logs = st.annotations["GLOBAL_LOGS"]
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

# Simple refresh trigger to redraw the UI state from global storage
st.button("🔄 Refresh Matrices")