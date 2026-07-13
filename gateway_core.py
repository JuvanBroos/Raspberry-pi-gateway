import serial
import serial.tools.list_ports
import threading
import time
import sys
import argparse
import struct
import os
import json
import paho.mqtt.client as mqtt

# Protocol Enums
PROTO_WIFI = 0
PROTO_ZIGBEE = 1
PROTO_LORA = 2

REPORT_TYPE_SENSOR_DATA = 0x01
REPORT_TYPE_NODE_STATUS = 0x02
REPORT_TYPE_LOG_MESSAGE = 0x03

REPORT_STRUCT_FMT = '<BBBBBHbI64sH'
REPORT_SIZE = struct.calcsize(REPORT_STRUCT_FMT)

UPLINK_PAYLOAD_FMT = '<6sHBHHBiiHHHH'
UPLINK_PAYLOAD_SIZE = struct.calcsize(UPLINK_PAYLOAD_FMT)

COMMAND_STRUCT_FMT = '<BBBBHHHH'

MQTT_BROKER = "localhost"
MQTT_PORT = 1883

class GatewayCore:
    def __init__(self, ports_config, baudrate):
        self.ports_config = ports_config
        self.baudrate = baudrate
        self.serial_conns = {}
        self.stop_event = threading.Event()
        
        # Route tracking map: node_id (int) -> proto_enum (int)
        self.node_routes = {}
        self.routes_lock = threading.Lock()

        # Initialize MQTT
        self.mqtt_client = mqtt.Client()
        self.mqtt_client.on_connect = self.on_mqtt_connect
        self.mqtt_client.on_message = self.on_mqtt_message
        self.mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
        self.mqtt_client.loop_start()

    def on_mqtt_connect(self, client, userdata, flags, rc):
        print(f"Core Engine connected to MQTT broker (code {rc})")
        self.mqtt_client.subscribe("gateway/commands")

    def on_mqtt_message(self, client, userdata, msg):
        try:
            cmd_data = json.loads(msg.payload.decode())
            self.process_downlink_intent(cmd_data)
        except Exception as e:
            print(f"Error handling MQTT command packet: {e}")

    def start(self):
        opened_ports = {}
        for proto, port in self.ports_config.items():
            if port and port not in opened_ports:
                try:
                    conn = serial.Serial(port, self.baudrate, timeout=0.1)
                    self.serial_conns[proto] = conn
                    opened_ports[port] = conn
                    threading.Thread(target=self.read_serial_data, args=(conn, proto), daemon=True).start()
                    p_name = {PROTO_WIFI: "WiFi/ESP-NOW", PROTO_ZIGBEE: "Zigbee", PROTO_LORA: "LoRa"}.get(proto, "UNK")
                    print(f"Opened {port} for {p_name} at {self.baudrate} bps")
                except Exception as e:
                    print(f"CRITICAL: Failed to open serial port {port}: {e}")
            elif port:
                self.serial_conns[proto] = opened_ports[port]

    def read_serial_data(self, conn, expected_proto):
        proto_name_str = {PROTO_WIFI: "WiFi/ESP-NOW", PROTO_ZIGBEE: "Zigbee", PROTO_LORA: "LoRa"}.get(expected_proto, "UNK")
        buffer = bytearray()
        while not self.stop_event.is_set():
            if conn and conn.is_open:
                try:
                    chunk = conn.read(1024)
                    if chunk:
                        buffer.extend(chunk)
                        while len(buffer) >= REPORT_SIZE:
                            if buffer[0] == 0xAA and buffer[1] == 0x55:
                                frame = buffer[:REPORT_SIZE]
                                self.parse_binary_frame(frame)
                                buffer = buffer[REPORT_SIZE:]
                            else:
                                try:
                                    end_idx = buffer.find(b'\n')
                                    if end_idx != -1:
                                        line = buffer[:end_idx+1].decode('utf-8', errors='ignore').strip()
                                        if line:
                                            self.mqtt_client.publish(f"gateway/raw_logs/{expected_proto}", line)
                                        buffer = buffer[end_idx+1:]
                                    else:
                                        if len(buffer) > 2048: buffer = buffer[1:]
                                        break
                                except:
                                    buffer = buffer[1:]
                except Exception as e:
                    print(f"Serial Read Error: {e}")
                    time.sleep(1)
            else:
                time.sleep(0.5)

    def parse_binary_frame(self, frame):
        try:
            calc_sum = sum(frame[:-2]) & 0xFFFF
            recv_sum = struct.unpack('<H', frame[-2:])[0]
            if calc_sum != recv_sum: return

            _, _, length, protocol, report_type, src_addr, rssi, ts, payload_raw, _ = struct.unpack(REPORT_STRUCT_FMT, frame)
            
            # Cache the known interface route for this node dynamically
            with self.routes_lock:
                self.node_routes[src_addr] = protocol

            topic_base = f"gateway/node/0x{src_addr:04X}"
            
            if report_type == REPORT_TYPE_SENSOR_DATA:
                ieee_raw, _, _, batt, temp, sats, lat, lon, curr, ax, ay, az = struct.unpack(UPLINK_PAYLOAD_FMT, payload_raw[:UPLINK_PAYLOAD_SIZE])
                proto_str = {PROTO_WIFI: "ESP-NOW", PROTO_ZIGBEE: "Zigbee", PROTO_LORA: "LoRa"}.get(protocol, "UNK")
                
                data = {
                    "ID": f"0x{src_addr:04X}", "Proto": proto_str, "RSSI": f"{rssi} dBm", "Batt": f"{batt}",
                    "Temp": f"{(temp/10.0)-273.15:.2f}", "GNSS": f"{sats}", "Lat": f"{lat/1000000.0:.6f}",
                    "Lon": f"{lon/1000000.0:.6f}", "Accel_X": ax, "Accel_Y": ay, "Accel_Z": az
                }
                self.mqtt_client.publish(f"{topic_base}/data", json.dumps(data))
                
            elif report_type == REPORT_TYPE_NODE_STATUS:
                self.mqtt_client.publish(f"{topic_base}/status", "Status Update")
            elif report_type == REPORT_TYPE_LOG_MESSAGE:
                msg = payload_raw.decode('utf-8', errors='ignore').strip('\x00')
                self.mqtt_client.publish(f"{topic_base}/logs", msg)
                
        except Exception as e:
            print(f"Frame parsing error: {e}")

    def process_downlink_intent(self, cmd_data):
        try:
            target_addr = int(cmd_data["target_addr"], 16)
            cmd_type = int(cmd_data["cmd_type"])
            
            # Auto-route calculation from cached routes, fallback to default provided
            with self.routes_lock:
                route_proto = self.node_routes.get(target_addr, int(cmd_data.get("fallback_proto", PROTO_WIFI)))

            command_value_1_min = 0
            command_value_2_max = 0
            command_value_3 = 0

            if cmd_type == 0x02: # CHANGE_PROTOCOL
                command_value_3 = int(cmd_data["target_proto_enum"])
            elif cmd_type == 0x03: # CHANGE_SLEEP_INTERVAL
                sleep_val = int(cmd_data["sleep_val"])
                if route_proto == PROTO_ZIGBEE:
                    command_value_1_min = 0
                    command_value_2_max = sleep_val
                    command_value_3 = 1000  # Delta value matching your protocol structure
                else:
                    command_value_3 = sleep_val
            elif cmd_type == 0x01: # READ_ATTRIBUTE
                command_value_3 = 0

            if route_proto not in self.serial_conns or not self.serial_conns[route_proto].is_open:
                print(f"Error: No open interface interface for protocol {route_proto}")
                return

            frame = struct.pack(COMMAND_STRUCT_FMT,
                0xAA, 0x55, route_proto, cmd_type, target_addr,
                command_value_1_min, command_value_2_max, command_value_3
            )
            
            self.serial_conns[route_proto].write(frame)
            print(f"Sent packed frame to 0x{target_addr:04X} via interface {route_proto}")
        except Exception as e:
            print(f"Failed to generate and transmit serial downlink frame: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--wifi")
    parser.add_argument("--zigbee")
    parser.add_argument("--lora")
    parser.add_argument("-b", "--baudrate", type=int, default=115200)
    args = parser.parse_args()

    ports_config = {}
    if args.wifi: ports_config[PROTO_WIFI] = args.wifi
    if args.zigbee: ports_config[PROTO_ZIGBEE] = args.zigbee
    if args.lora: ports_config[PROTO_LORA] = args.lora

    if not ports_config:
        port = serial.tools.list_ports.comports()[-1].device if serial.tools.list_ports.comports() else None
        if not port: sys.exit(1)
        ports_config[PROTO_WIFI] = port

    core = GatewayCore(ports_config, args.baudrate)
    core.start()
    try:
        while True: time.sleep(1)
    except KeyboardInterrupt:
        core.stop_event.set()