import serial
import serial.tools.list_ports
import threading
import time
import sys
import argparse
import struct
import json
import paho.mqtt.client as mqtt

# Protocol Enums
PROTO_WIFI = 0
PROTO_ZIGBEE = 1
PROTO_LORA = 2

# Report Types
REPORT_TYPE_SENSOR_DATA = 0x01
REPORT_TYPE_NODE_STATUS = 0x02
REPORT_TYPE_LOG_MESSAGE = 0x03

# Struct Formats
REPORT_STRUCT_FMT = '<BBBBBHbI64sH'
REPORT_SIZE = struct.calcsize(REPORT_STRUCT_FMT)

UPLINK_PAYLOAD_FMT = '<6sHBHHBiiHHHH'
UPLINK_PAYLOAD_SIZE = struct.calcsize(UPLINK_PAYLOAD_FMT)

COMMAND_STRUCT_FMT = '<BBBBHHHH'
COMMAND_SIZE = struct.calcsize(COMMAND_STRUCT_FMT)

MQTT_BROKER = "localhost"
MQTT_PORT = 1883
MQTT_TOPIC_UPLINK_DATA = "wsn/uplink/data"
MQTT_TOPIC_UPLINK_STATUS = "wsn/uplink/status"
MQTT_TOPIC_UPLINK_LOG = "wsn/uplink/log"
MQTT_TOPIC_DOWNLINK_CMD = "wsn/downlink/command"

class GatewayMQTTBridge:
    def __init__(self, ports_config, baudrate):
        self.ports_config = ports_config
        self.baudrate = baudrate
        self.serial_conns = {}
        self.stop_event = threading.Event()
        
        # Setup MQTT
        self.mqtt_client = mqtt.Client()
        self.mqtt_client.on_connect = self.on_mqtt_connect
        self.mqtt_client.on_message = self.on_mqtt_message
        
    def start(self):
        print(f"Connecting to MQTT broker at {MQTT_BROKER}:{MQTT_PORT}...")
        try:
            self.mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
            self.mqtt_client.loop_start()
        except Exception as e:
            print(f"Failed to connect to MQTT: {e}")
            sys.exit(1)

        self.start_serial_threads()
        
        try:
            while not self.stop_event.is_set():
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nShutting down...")
            self.stop()

    def stop(self):
        self.stop_event.set()
        self.mqtt_client.loop_stop()
        self.mqtt_client.disconnect()
        for conn in self.serial_conns.values():
            if conn and conn.is_open:
                conn.close()

    def on_mqtt_connect(self, client, userdata, flags, rc):
        if rc == 0:
            print("Connected to MQTT Broker!")
            client.subscribe(MQTT_TOPIC_DOWNLINK_CMD)
            print(f"Subscribed to {MQTT_TOPIC_DOWNLINK_CMD}")
        else:
            print(f"Failed to connect to MQTT, return code {rc}")

    def on_mqtt_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
            print(f"Received MQTT command: {payload}")
            self.send_command(payload)
        except json.JSONDecodeError:
            print(f"Invalid JSON received on {msg.topic}")
        except Exception as e:
            print(f"Error processing MQTT command: {e}")

    def start_serial_threads(self):
        opened_ports = {}
        for proto, port in self.ports_config.items():
            if port:
                if port not in opened_ports:
                    try:
                        conn = serial.Serial(port, self.baudrate, timeout=0.1)
                        self.serial_conns[proto] = conn
                        opened_ports[port] = conn
                        thread = threading.Thread(target=self.read_serial_data, args=(conn, proto), daemon=True)
                        thread.start()
                        proto_name = {PROTO_WIFI: "WiFi/ESP-NOW", PROTO_ZIGBEE: "Zigbee", PROTO_LORA: "LoRa"}.get(proto, "UNK")
                        print(f"Opened {port} for {proto_name} at {self.baudrate} bps")
                    except Exception as e:
                        print(f"CRITICAL: Failed to open serial port {port}: {e}")
                else:
                    self.serial_conns[proto] = opened_ports[port]
                    proto_name = {PROTO_WIFI: "WiFi/ESP-NOW", PROTO_ZIGBEE: "Zigbee", PROTO_LORA: "LoRa"}.get(proto, "UNK")
                    print(f"Shared {port} for {proto_name} at {self.baudrate} bps")

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
                                        line_bytes = buffer[:end_idx+1]
                                        line = line_bytes.decode('utf-8', errors='ignore').strip()
                                        if line:
                                            log_payload = {
                                                "protocol": proto_name_str,
                                                "message": line
                                            }
                                            self.mqtt_client.publish(MQTT_TOPIC_UPLINK_LOG, json.dumps(log_payload))
                                        buffer = buffer[end_idx+1:]
                                    else:
                                        if len(buffer) > 2048:
                                            buffer = buffer[1:]
                                        break
                                except:
                                    buffer = buffer[1:]
                except Exception as e:
                    print(f"Read error: {e}")
                    time.sleep(1)
            else:
                time.sleep(0.5)

    def parse_binary_frame(self, frame):
        try:
            calc_sum = sum(frame[:-2]) & 0xFFFF
            recv_sum = struct.unpack('<H', frame[-2:])[0]
            
            if calc_sum != recv_sum:
                return

            _, _, length, protocol, report_type, src_addr, rssi, ts, payload_raw, _ = struct.unpack(REPORT_STRUCT_FMT, frame)
            proto_str = {PROTO_WIFI: "WiFi/ESP-NOW", PROTO_ZIGBEE: "Zigbee", PROTO_LORA: "LoRa"}.get(protocol, "UNK")
            
            if report_type == REPORT_TYPE_SENSOR_DATA:
                self.parse_sensor_data(src_addr, proto_str, rssi, payload_raw)
            elif report_type == REPORT_TYPE_NODE_STATUS:
                status_payload = {
                    "node_id": f"0x{src_addr:04X}",
                    "protocol": proto_str,
                    "status": "Update"
                }
                self.mqtt_client.publish(MQTT_TOPIC_UPLINK_STATUS, json.dumps(status_payload))
                print(f"Published status for 0x{src_addr:04X}")
            elif report_type == REPORT_TYPE_LOG_MESSAGE:
                msg = payload_raw.decode('utf-8', errors='ignore').strip('\x00')
                log_payload = {
                    "node_id": f"0x{src_addr:04X}",
                    "protocol": proto_str,
                    "message": msg
                }
                self.mqtt_client.publish(MQTT_TOPIC_UPLINK_LOG, json.dumps(log_payload))
                print(f"Published log from 0x{src_addr:04X}")
                
        except Exception as e:
            print(f"Parse error: {e}")

    def parse_sensor_data(self, src_addr, proto_str, rssi, payload_raw):
        try:
            ieee_raw, _, _, batt, temp, sats, lat, lon, curr, ax, ay, az = struct.unpack(UPLINK_PAYLOAD_FMT, payload_raw[:UPLINK_PAYLOAD_SIZE])
            
            data = {
                "node_id": f"0x{src_addr:04X}",
                "protocol": proto_str,
                "rssi": rssi,
                "batt_mv": batt * 2,
                "temp_c": round(temp / 10.0 - 273.15, 2),
                "gnss_sats": sats,
                "lat": round(lat / 1000000.0, 6),
                "lon": round(lon / 1000000.0, 6),
                "accel_x": round((ax - 1650) / 330, 2),
                "accel_y": round((ay - 1650) / 330, 2),
                "accel_z": round((az - 1650) / 330, 2),
                "timestamp_ms": int(time.time() * 1000)
            }
            
            self.mqtt_client.publish(MQTT_TOPIC_UPLINK_DATA, json.dumps(data))
            print(f"Published data for {data['node_id']}")
        except Exception as e:
            print(f"Uplink unpack error: {e}")

    def send_command(self, payload):
        try:
            target_addr_str = payload.get("target_addr", "")
            if isinstance(target_addr_str, str) and target_addr_str.startswith("0x"):
                target_addr = int(target_addr_str, 16)
            else:
                target_addr = int(target_addr_str)
                
            cmd_type = int(payload.get("cmd_type", 0x02))
            route_proto = int(payload.get("proto", PROTO_WIFI))
            
            command_value_1_min = int(payload.get("val1", 0))
            command_value_2_max = int(payload.get("val2", 0))
            command_value_3 = int(payload.get("val3", 0))
            
            if cmd_type == 0x03: # CHANGE_SLEEP_INTERVAL
                if route_proto == PROTO_ZIGBEE:
                    command_value_1_min = 0
                    command_value_2_max = command_value_3 # Sleep value is typically passed in val3
                    command_value_3 = 1 # Make this 1 to use the zigbee counter for rapid reporting
            
            if route_proto not in self.serial_conns or not self.serial_conns[route_proto].is_open:
                print(f"ERROR: Cannot route command to 0x{target_addr:04X}. No active serial connection for protocol {route_proto}.")
                return
            
            frame = struct.pack(COMMAND_STRUCT_FMT,
                0xAA, 0x55,
                route_proto,
                cmd_type,
                target_addr,
                command_value_1_min,
                command_value_2_max,
                command_value_3
            )
            
            self.serial_conns[route_proto].write(frame)
            print(f"Sent binary command {cmd_type} to 0x{target_addr:04X} via protocol {route_proto}")

        except Exception as e:
            print(f"ERROR formatting/sending command: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Raspberry Pi MQTT Serial Bridge")
    parser.add_argument("--wifi", type=str, help="Serial Port for WiFi/ESP-NOW Gateway (e.g., /dev/ttyUSB0)")
    parser.add_argument("--zigbee", type=str, help="Serial Port for Zigbee Gateway (e.g., /dev/ttyUSB1)")
    parser.add_argument("--lora", type=str, help="Serial Port for LoRa Gateway (e.g., /dev/ttyUSB2)")
    parser.add_argument("-b", "--baudrate", type=int, default=115200, help="Baudrate")
    args = parser.parse_args()

    ports_config = {}
    if args.wifi: ports_config[PROTO_WIFI] = args.wifi
    if args.zigbee: ports_config[PROTO_ZIGBEE] = args.zigbee
    if args.lora: ports_config[PROTO_LORA] = args.lora

    if not ports_config:
        print("No specific protocol ports provided. Please specify at least one, e.g., --wifi /dev/ttyUSB0")
        sys.exit(1)

    bridge = GatewayMQTTBridge(ports_config=ports_config, baudrate=args.baudrate)
    bridge.start()
