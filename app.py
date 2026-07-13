import streamlit as st
import paho.mqtt.client as mqtt
import json
import pandas as pd
from datetime import datetime

MQTT_BROKER = "localhost"
MQTT_PORT = 1883

st.set_page_config(layout="wide", page_title="Sensor Coordinator UI")

# --- Initialize Global Session States ---
if "nodes" not in st.session_state:
    st.session_state.nodes = {}
if "logs" not in st.session_state:
    st.session_state.logs = []

def on_connect(client, userdata, flags, rc):
    client.subscribe("gateway/node/+/data")
    client.subscribe("gateway/node/+/status")
    client.subscribe("gateway/node/+/logs")
    client.subscribe("gateway/raw_logs/+")

def on_message(client, userdata, msg):
    # --- Thread-Safe Safety Initialization Guards ---
    if "logs" not in st.session_state:
        st.session_state.logs = []
    if "nodes" not in st.session_state:
        st.session_state.nodes = {}

    parts = msg.topic.split('/')
    timestamp = datetime.now().strftime("%H:%M:%S")
    
    if "raw_logs" in msg.topic:
        proto_id = parts[2]
        proto_name = {"0": "WiFi", "1": "Zigbee", "2": "LoRa"}.get(proto_id, "UNK")
        st.session_state.logs.append(f"[{timestamp}] [{proto_name} Raw] {msg.payload.decode()}")
    else:
        node_id = parts[2]
        sub_type = parts[3]
        
        if sub_type == "data":
            data = json.loads(msg.payload.decode())
            data["Last Seen"] = timestamp
            st.session_state.nodes[node_id] = data
        elif sub_type == "status":
            st.session_state.logs.append(f"[{timestamp}] [{node_id}] Node Status Frame Received")
        elif sub_type == "logs":
            st.session_state.logs.append(f"[{timestamp}] [{node_id}] {msg.payload.decode()}")

    # Keep log collection size capped
    if len(st.session_state.logs) > 60:
        st.session_state.logs.pop(0)

@st.cache_resource
def start_mqtt():
    # Using explicit callback API version to suppress your line 57 deprecation warning
    client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION1)
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(MQTT_BROKER, MQTT_PORT, 60)
    client.loop_start()
    
    # --- Inject Streamlit Context into the background thread loop ---
    for thread in threading.enumerate():
        if "paho-mqtt-client" in thread.name:
            add_script_run_context(thread)
            
    return client

mqtt_client = start_mqtt()

st.title("📡 Multi-Protocol Coordinator Cockpit")

col_left, col_right = st.columns([3, 1])

with col_left:
    st.subheader("Discovered Node Telemetry")
    if st.session_state.nodes:
        raw_df = pd.DataFrame.from_dict(st.session_state.nodes, orient='index')
        
        # Combine Accel fields for UI uniformity matching your list columns
        if 'Accel_X' in raw_df.columns:
            raw_df['Accel X/Y/Z'] = raw_df.apply(lambda r: f"{r['Accel_X']}/{r['Accel_Y']}/{r['Accel_Z']}", axis=1)
            display_df = raw_df.reindex(columns=["Proto", "RSSI", "Batt", "Temp", "GNSS", "Lat", "Lon", "Accel X/Y/Z", "Last Seen"])
        else:
            display_df = raw_df
            
        st.dataframe(display_df, use_container_width=True)
    else:
        st.info("Scanning for incoming network telemetry matrices...")

    st.subheader("Network Event Log Stream")
    if st.session_state.logs:
        log_block = "\n".join(reversed(st.session_state.logs))
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
                st.success(f"Downlink instruction published for core processing loop.")

if st.button("🔄 Refresh Matrices"):
    st.rerun()