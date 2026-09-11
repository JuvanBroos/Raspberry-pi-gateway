import serial  # Import PySerial library to communicate with hardware serial ports (USB/UART)
import serial.tools.list_ports  # Import PySerial utility module for listing available serial hardware ports
import threading  # Import threading module to run concurrent background threads for serial port listening
import time  # Import time module to handle time delays, sleep intervals, and timestamp generation
import sys  # Import sys module to access command-line parameters and terminate the process via sys.exit()
import argparse  # Import argparse module to parse command-line flags and arguments passed to the script
import struct  # Import struct module to pack and unpack binary data buffers according to C-style struct formats
import json  # Import json module to serialize Python data into JSON strings and deserialize JSON strings
import paho.mqtt.client as mqtt  # Import Eclipse Paho MQTT client library and alias it as 'mqtt'
# ----------------------------------------------------------------------------------------------------------------------
# Protocol Numeric Identifiers (Constants)
# ----------------------------------------------------------------------------------------------------------------------
PROTO_WIFI = 0  # Numeric identifier representing the WiFi / ESP-NOW wireless protocol (0)
PROTO_ZIGBEE = 1  # Numeric identifier representing the Zigbee mesh networking protocol (1)
PROTO_LORA = 2  # Numeric identifier representing the LoRa long-range wireless protocol (2)
# ----------------------------------------------------------------------------------------------------------------------
# Packet Report Type Identifiers (Hexadecimal Constants)
# ----------------------------------------------------------------------------------------------------------------------
REPORT_TYPE_SENSOR_DATA = 0x01  # Frame type byte (1) indicating the payload contains sensor telemetry data
REPORT_TYPE_NODE_STATUS = 0x02  # Frame type byte (2) indicating the payload contains node status/heartbeat info
REPORT_TYPE_LOG_MESSAGE = 0x03  # Frame type byte (3) indicating the payload contains a text log/debug string
# ----------------------------------------------------------------------------------------------------------------------
# Binary Struct Definitions and Calculated Byte Sizes
# ----------------------------------------------------------------------------------------------------------------------
REPORT_STRUCT_FMT = '<BBBBBHbI64sH'  # Little-endian struct format string describing the binary gateway report header
REPORT_SIZE = struct.calcsize(REPORT_STRUCT_FMT)  # Calculate total byte size of the binary report header structure
# ----------------------------------------------------------------------------------------------------------------------
UPLINK_PAYLOAD_FMT = '<6sHBHHBiiHHHH'  # Little-endian struct format string describing raw telemetry sensor data payload
UPLINK_PAYLOAD_SIZE = struct.calcsize(UPLINK_PAYLOAD_FMT)  # Calculate total byte size of the raw sensor data payload
# ----------------------------------------------------------------------------------------------------------------------
COMMAND_STRUCT_FMT = '<BBBBHHHH'  # Little-endian struct format string describing downlink commands sent to nodes
COMMAND_SIZE = struct.calcsize(COMMAND_STRUCT_FMT)  # Calculate total byte size of the downlink command structure
# ----------------------------------------------------------------------------------------------------------------------
# MQTT Broker and Topic Configuration
# ----------------------------------------------------------------------------------------------------------------------
MQTT_BROKER = "localhost"  # Hostname/IP of the MQTT broker (local Raspberry Pi running Mosquitto)
MQTT_PORT = 1883  # TCP port number for standard MQTT messaging (default 1883)
MQTT_TOPIC_UPLINK_DATA = "wsn/uplink/data"  # Topic string where processed sensor readings are published
MQTT_TOPIC_UPLINK_STATUS = "wsn/uplink/status"  # Topic string where node lifecycle/status changes are published
MQTT_TOPIC_UPLINK_LOG = "wsn/uplink/log"  # Topic string where raw text logs and debug prints are published
MQTT_TOPIC_DOWNLINK_CMD = "wsn/downlink/command"  # Topic string where downstream commands to nodes are received
# ----------------------------------------------------------------------------------------------------------------------
# GatewayMQTTBridge Class
# ----------------------------------------------------------------------------------------------------------------------
class GatewayMQTTBridge:  # Define class to bridge hardware serial ports and the MQTT broker
    def __init__(self, ports_config, baudrate):  # Constructor method taking a port mapping dictionary and baudrate integer
        self.ports_config = ports_config  # Store dictionary mapping protocol IDs to serial port paths (e.g. {0: '/dev/ttyUSB0'})
        self.baudrate = baudrate  # Store communication speed in bits per second (e.g. 115200)
        self.serial_conns = {}  # Initialize empty dictionary to hold active serial port connection objects
        self.stop_event = threading.Event()  # Create a thread synchronization flag used to signal all threads to stop
        self.mqtt_client = mqtt.Client()  # Instantiate an MQTT client instance for network messaging
        self.mqtt_client.on_connect = self.on_mqtt_connect  # Bind the on_connect callback to handle successful MQTT broker connection
        self.mqtt_client.on_message = self.on_mqtt_message  # Bind the on_message callback to handle incoming MQTT commands
# ----------------------------------------------------------------------------------------------------------------------
    def start(self):  # Define method to start MQTT client, spawn serial reader threads, and monitor execution
        print(f"Connecting to MQTT broker at {MQTT_BROKER}:{MQTT_PORT}...")  # Print target MQTT connection details to terminal
        try:  # Begin try block to handle any failure during MQTT broker connection attempt
            self.mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)  # Connect to MQTT broker with a 60-second keepalive timer
            self.mqtt_client.loop_start()  # Start a non-blocking background thread managed by Paho MQTT to service the network
        except Exception as e:  # Catch any exception occurring during MQTT connection attempt
            print(f"Failed to connect to MQTT: {e}")  # Output connection error message to terminal
            sys.exit(1)  # Terminate execution immediately with error code 1
        self.start_serial_threads()  # Call method to open serial ports and launch listening threads for each protocol
        try:  # Begin try block to maintain main thread alive until stopped or interrupted
            while not self.stop_event.is_set():  # Loop continuously as long as the stop_event flag has not been signaled
                time.sleep(1)  # Sleep for 1 second in each loop iteration to avoid high CPU usage
        except KeyboardInterrupt:  # Catch user pressing Ctrl+C in the terminal
            print("\nShutting down...")  # Output shutdown notification to terminal
            self.stop()  # Invoke stop method to safely terminate all background activities and connections
# ----------------------------------------------------------------------------------------------------------------------
    def stop(self):  # Define method to cleanly disconnect and close all serial ports and MQTT connections
        self.stop_event.set()  # Signal the threading event flag to tell all serial worker threads to exit loops
        self.mqtt_client.loop_stop()  # Stop the background MQTT network processing thread
        self.mqtt_client.disconnect()  # Cleanly disconnect the MQTT client from the broker
        for conn in self.serial_conns.values():  # Iterate through all active serial connection objects in dictionary
            if conn and conn.is_open:  # Check if serial connection exists and is currently open
                conn.close()  # Close the physical serial connection port
# ----------------------------------------------------------------------------------------------------------------------
    def on_mqtt_connect(self, client, userdata, flags, rc):  # Define callback function invoked when MQTT broker responds to connection
        if rc == 0:  # Check if return code is 0, meaning broker accepted connection successfully
            print("Connected to MQTT Broker!")  # Display successful connection message on console
            client.subscribe(MQTT_TOPIC_DOWNLINK_CMD)  # Subscribe to downlink command topic to receive instructions for nodes
            print(f"Subscribed to {MQTT_TOPIC_DOWNLINK_CMD}")  # Display subscription confirmation on console
        else:  # Return code was non-zero, indicating a connection failure
            print(f"Failed to connect to MQTT, return code {rc}")  # Print failure message and numeric error code
# ----------------------------------------------------------------------------------------------------------------------
    def on_mqtt_message(self, client, userdata, msg):  # Define callback function invoked when message arrives on subscribed topic
        try:  # Begin try block to handle JSON decoding and command dispatching safely
            payload = json.loads(msg.payload.decode())  # Decode message byte array to string and parse JSON into Python dictionary
            print(f"Received MQTT command: {payload}")  # Log received command dictionary to console
            self.send_command(payload)  # Pass the parsed command payload to the send_command method for serial delivery
        except json.JSONDecodeError:  # Handle case where payload cannot be parsed as valid JSON
            print(f"Invalid JSON received on {msg.topic}")  # Print error indicating JSON formatting was invalid
        except Exception as e:  # Catch any other unforeseen error during message reception
            print(f"Error processing MQTT command: {e}")  # Print general error description to console
# ----------------------------------------------------------------------------------------------------------------------
    def start_serial_threads(self):  # Define method to open configured serial ports and launch reading threads
        opened_ports = {}  # Create local dictionary to track already opened ports and avoid opening the same port twice
        for proto, port in self.ports_config.items():  # Loop through protocol ID and port path pairs in the configuration
            if port:  # Ensure the port string is not empty or None
                if port not in opened_ports:  # Check if this physical port path has not yet been opened
                    try:  # Begin try block to handle port access permissions or missing device errors
                        conn = serial.Serial(port, self.baudrate, timeout=0.1)  # Open serial port with baudrate and 100ms read timeout
                        self.serial_conns[proto] = conn  # Map protocol ID to the newly opened serial connection object
                        opened_ports[port] = conn  # Save open connection in opened_ports cache dictionary
                        thread = threading.Thread(target=self.read_serial_data, args=(conn, proto), daemon=True)  # Create background daemon thread
                        thread.start()  # Launch thread to continuously execute read_serial_data
                        proto_name = {PROTO_WIFI: "WiFi/ESP-NOW", PROTO_ZIGBEE: "Zigbee", PROTO_LORA: "LoRa"}.get(proto, "UNK")  # Lookup protocol name
                        print(f"Opened {port} for {proto_name} at {self.baudrate} bps")  # Log port opening confirmation
                    except Exception as e:  # Catch any exception encountered when opening serial port
                        print(f"CRITICAL: Failed to open serial port {port}: {e}")  # Log critical failure message to console
                else:  # Port path is already open (multiple protocols sharing the same physical hardware port)
                    self.serial_conns[proto] = opened_ports[port]  # Assign existing open connection to this protocol
                    proto_name = {PROTO_WIFI: "WiFi/ESP-NOW", PROTO_ZIGBEE: "Zigbee", PROTO_LORA: "LoRa"}.get(proto, "UNK")  # Lookup protocol name
                    print(f"Shared {port} for {proto_name} at {self.baudrate} bps")  # Log sharing message to console
# ----------------------------------------------------------------------------------------------------------------------
    def read_serial_data(self, conn, expected_proto):  # Define method executed in separate thread to read and buffer serial bytes
        proto_name_str = {PROTO_WIFI: "WiFi/ESP-NOW", PROTO_ZIGBEE: "Zigbee", PROTO_LORA: "LoRa"}.get(expected_proto, "UNK")  # Lookup protocol name string
        buffer = bytearray()  # Initialize mutable byte array buffer to accumulate raw incoming bytes
        while not self.stop_event.is_set():  # Loop until the stop event is signaled
            if conn and conn.is_open:  # Verify connection object exists and serial port is open
                try:  # Begin try block to catch any serial communication read errors
                    chunk = conn.read(1024)  # Read up to 1024 bytes from the serial port buffer
                    if chunk:  # Check if any bytes were actually read (chunk is not empty)
                        buffer.extend(chunk)  # Append newly received bytes to the end of the bytearray buffer
                        while len(buffer) >= REPORT_SIZE:  # Process all complete binary frames available in buffer
                            if buffer[0] == 0xAA and buffer[1] == 0x55:  # Check for sync header magic bytes 0xAA and 0x55
                                frame = buffer[:REPORT_SIZE]  # Slice out the exact byte length of one binary report frame
                                self.parse_binary_frame(frame)  # Parse and handle the binary frame
                                buffer = buffer[REPORT_SIZE:]  # Remove the processed frame from front of the buffer
                            else:  # Buffer start does not match binary frame sync header; could be ASCII text log
                                try:  # Begin try block to check for newline-delimited text log lines
                                    end_idx = buffer.find(b'\n')  # Search for newline byte in the buffer
                                    if end_idx != -1:  # If newline character was found in buffer
                                        line_bytes = buffer[:end_idx+1]  # Slice the full text line including the newline byte
                                        line = line_bytes.decode('utf-8', errors='ignore').strip()  # Decode bytes to UTF-8 text and strip whitespace
                                        if line:  # Check if stripped line contains text
                                            log_payload = {  # Construct dictionary containing protocol and log message text
                                                "protocol": proto_name_str,  # Set protocol name
                                                "message": line  # Set decoded message string
                                            }  # Close dictionary literal
                                            self.mqtt_client.publish(MQTT_TOPIC_UPLINK_LOG, json.dumps(log_payload))  # Publish log to MQTT as JSON
                                        buffer = buffer[end_idx+1:]  # Discard the parsed text line from the buffer
                                    else:  # No newline character found yet in remaining buffer
                                        if len(buffer) > 2048:  # Check if unparsed junk buffer exceeds 2048 bytes
                                            buffer = buffer[1:]  # Discard oldest byte to prevent buffer from growing indefinitely
                                        break  # Break inner loop to await more data from serial port
                                except:  # Handle any decoding or slicing exception
                                    buffer = buffer[1:]  # Discard single problematic byte from buffer
                except Exception as e:  # Handle serial read hardware errors or port disconnections
                    print(f"Read error: {e}")  # Print read error details to console
                    time.sleep(1)  # Wait 1 second before retrying to avoid rapid error loops
            else:  # Serial connection is not open
                time.sleep(0.5)  # Wait half a second before checking connection status again
# ----------------------------------------------------------------------------------------------------------------------
    def parse_binary_frame(self, frame):  # Define method to validate checksum and parse decoded binary frames
        try:  # Begin try block to handle any binary unpacking or decoding errors
            calc_sum = sum(frame[:-2]) & 0xFFFF  # Calculate 16-bit sum of all bytes except the trailing 2 checksum bytes
            recv_sum = struct.unpack('<H', frame[-2:])[0]  # Unpack last two bytes as unsigned 16-bit little-endian integer
            if calc_sum != recv_sum:  # Compare calculated checksum with received checksum
                return  # If checksum mismatch occurs, discard corrupt frame and return immediately
            _, _, length, protocol, report_type, src_addr, rssi, ts, payload_raw, _ = struct.unpack(REPORT_STRUCT_FMT, frame)  # Unpack binary frame fields
            proto_str = {PROTO_WIFI: "WiFi/ESP-NOW", PROTO_ZIGBEE: "Zigbee", PROTO_LORA: "LoRa"}.get(protocol, "UNK")  # Map protocol number to string
            if report_type == REPORT_TYPE_SENSOR_DATA:  # Check if frame contains sensor telemetry readings
                self.parse_sensor_data(src_addr, proto_str, rssi, payload_raw)  # Call helper method to unpack and publish sensor data
            elif report_type == REPORT_TYPE_NODE_STATUS:  # Check if frame contains node status update
                status_payload = {  # Construct dictionary representing status notification
                    "node_id": f"0x{src_addr:04X}",  # Format 16-bit address as uppercase 4-digit hexadecimal string (e.g. 0x00A1)
                    "protocol": proto_str,  # Include human-readable protocol name
                    "status": "Update"  # Set status text string
                }  # Close status dictionary literal
                self.mqtt_client.publish(MQTT_TOPIC_UPLINK_STATUS, json.dumps(status_payload))  # Publish status JSON to MQTT broker
                print(f"Published status for 0x{src_addr:04X}")  # Log publication status to console
            elif report_type == REPORT_TYPE_LOG_MESSAGE:  # Check if frame contains text log message
                msg = payload_raw.decode('utf-8', errors='ignore').strip('\x00')  # Decode raw bytes to UTF-8 text and strip null terminators
                log_payload = {  # Construct dictionary representing log message payload
                    "node_id": f"0x{src_addr:04X}",  # Format source address as 4-digit hexadecimal string
                    "protocol": proto_str,  # Include protocol name
                    "message": msg  # Include decoded text message
                }  # Close log dictionary literal
                self.mqtt_client.publish(MQTT_TOPIC_UPLINK_LOG, json.dumps(log_payload))  # Publish log JSON to MQTT broker
                print(f"Published log from 0x{src_addr:04X}")  # Log publication event to console
        except Exception as e:  # Catch any exception during binary parsing
            print(f"Parse error: {e}")  # Print parsing error details to console
# ----------------------------------------------------------------------------------------------------------------------
    def parse_sensor_data(self, src_addr, proto_str, rssi, payload_raw):  # Define method to unpack telemetry sensor payload
        try:  # Begin try block to handle struct unpacking and mathematical conversions safely
            ieee_raw, _, _, batt, temp, sats, lat, lon, curr, ax, ay, az = struct.unpack(UPLINK_PAYLOAD_FMT, payload_raw[:UPLINK_PAYLOAD_SIZE])  # Unpack fields
            data = {  # Construct dictionary with converted, human-readable telemetry values
                "node_id": f"0x{src_addr:04X}",  # Format source address as 4-digit hex string (e.g., '0x1A2B')
                "protocol": proto_str,  # Protocol string (e.g. 'WiFi/ESP-NOW', 'Zigbee', 'LoRa')
                "rssi": rssi,  # Signal strength indicator in dBm
                "batt_mv": batt * 2,  # Calculate battery voltage in millivolts (sensor sent half-voltage)
                "temp_c": round(temp / 10.0 - 273.15, 2),  # Convert tenths-of-Kelvin to Celsius rounded to 2 decimals
                "gnss_sats": sats,  # Number of visible GPS/GNSS satellites
                "lat": round(lat / 1000000.0, 6),  # Convert micro-degrees latitude to degrees rounded to 6 decimal places
                "lon": round(lon / 1000000.0, 6),  # Convert micro-degrees longitude to degrees rounded to 6 decimal places
                "accel_x": round((ax - 1650) / 330, 2),  # Convert raw analog accelerometer X value to G-force
                "accel_y": round((ay - 1650) / 330, 2),  # Convert raw analog accelerometer Y value to G-force
                "accel_z": round((az - 1650) / 330, 2),  # Convert raw analog accelerometer Z value to G-force
                "timestamp_ms": int(time.time() * 1000)  # Record current time in milliseconds since epoch
            }  # Close telemetry data dictionary literal
            self.mqtt_client.publish(MQTT_TOPIC_UPLINK_DATA, json.dumps(data))  # Serialize telemetry dictionary to JSON and publish to MQTT
            print(f"Published data for {data['node_id']}")  # Log publication success to terminal
        except Exception as e:  # Catch any exception during sensor unpacking or mathematical formulas
            print(f"Uplink unpack error: {e}")  # Print unpacking error message to console
# ----------------------------------------------------------------------------------------------------------------------
    def send_command(self, payload):  # Define method to format and transmit binary downlink command packet over serial
        try:  # Begin try block to handle command formatting and serial transmission errors
            target_addr_str = payload.get("target_addr", "")  # Extract target address string from incoming command dictionary
            if isinstance(target_addr_str, str) and target_addr_str.startswith("0x"):  # Check if target address is hex string like '0x1234'
                target_addr = int(target_addr_str, 16)  # Parse hexadecimal string into integer using base 16
            else:  # Target address is already an integer or decimal string
                target_addr = int(target_addr_str)  # Convert to integer using standard base 10
            cmd_type = int(payload.get("cmd_type", 0x02))  # Extract command type number, defaulting to 0x02 (PING/STATUS)
            route_proto = int(payload.get("proto", PROTO_WIFI))  # Extract target protocol ID, defaulting to PROTO_WIFI (0)
            command_value_1_min = int(payload.get("val1", 0))  # Extract integer parameter 1 (minimum value), default 0
            command_value_2_max = int(payload.get("val2", 0))  # Extract integer parameter 2 (maximum value), default 0
            command_value_3 = int(payload.get("val3", 0))  # Extract integer parameter 3 (additional value), default 0
            if cmd_type == 0x03:  # Check if command type is 0x03 (CHANGE_SLEEP_INTERVAL)
                if route_proto == PROTO_ZIGBEE:  # Check if target protocol is Zigbee
                    command_value_1_min = 0  # For Zigbee sleep intervals, set minimum interval to 0
                    command_value_2_max = command_value_3  # Set maximum sleep value from val3 parameter
                    command_value_3 = 1  # Set flag to 1 to enable Zigbee rapid reporting counter
            if route_proto not in self.serial_conns or not self.serial_conns[route_proto].is_open:  # Verify active connection exists for protocol
                print(f"ERROR: Cannot route command to 0x{target_addr:04X}. No active serial connection for protocol {route_proto}.")  # Error output
                return  # Abort command dispatch if no open serial port is available for that protocol
            frame = struct.pack(COMMAND_STRUCT_FMT,  # Pack command fields into binary buffer using COMMAND_STRUCT_FMT format
                0xAA, 0x55,  # Magic sync start bytes (0xAA, 0x55) indicating start of command packet
                route_proto,  # Protocol identifier byte (0: WiFi, 1: Zigbee, 2: LoRa)
                cmd_type,  # Command opcode byte
                target_addr,  # 16-bit destination node address
                command_value_1_min,  # 16-bit parameter value 1
                command_value_2_max,  # 16-bit parameter value 2
                command_value_3  # 16-bit parameter value 3
            )  # Close pack arguments and function call
            self.serial_conns[route_proto].write(frame)  # Transmit the packed binary command frame through the designated serial port
            print(f"Sent binary command {cmd_type} to 0x{target_addr:04X} via protocol {route_proto}")  # Log confirmation to console
        except Exception as e:  # Catch any exception during command parsing or transmission
            print(f"ERROR formatting/sending command: {e}")  # Output error details to terminal
# ----------------------------------------------------------------------------------------------------------------------
# Command-Line Argument Parsing and Entry Point
# ----------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":  # Check if script is run directly from command line (not imported as a module)
    parser = argparse.ArgumentParser(description="Raspberry Pi MQTT Serial Bridge")  # Create command-line argument parser with description
    parser.add_argument("--wifi", type=str, help="Serial Port for WiFi/ESP-NOW Gateway (e.g., /dev/ttyUSB0)")  # Add --wifi flag argument
    parser.add_argument("--zigbee", type=str, help="Serial Port for Zigbee Gateway (e.g., /dev/ttyUSB1)")  # Add --zigbee flag argument
    parser.add_argument("--lora", type=str, help="Serial Port for LoRa Gateway (e.g., /dev/ttyUSB2)")  # Add --lora flag argument
    parser.add_argument("-b", "--baudrate", type=int, default=115200, help="Baudrate")  # Add -b / --baudrate argument defaulting to 115200
    args = parser.parse_args()  # Parse all command-line arguments provided by the user
    ports_config = {}  # Initialize empty dictionary to hold mapping between protocol IDs and serial port paths
    if args.wifi: ports_config[PROTO_WIFI] = args.wifi  # If --wifi port was supplied, map PROTO_WIFI (0) to that port path
    if args.zigbee: ports_config[PROTO_ZIGBEE] = args.zigbee  # If --zigbee port was supplied, map PROTO_ZIGBEE (1) to that port path
    if args.lora: ports_config[PROTO_LORA] = args.lora  # If --lora port was supplied, map PROTO_LORA (2) to that port path
    if not ports_config:  # Check if no ports were specified (ports_config dictionary is empty)
        print("No specific protocol ports provided. Please specify at least one, e.g., --wifi /dev/ttyUSB0")  # Inform user how to supply flags
        sys.exit(1)  # Terminate execution with error status code 1
    bridge = GatewayMQTTBridge(ports_config=ports_config, baudrate=args.baudrate)  # Instantiate GatewayMQTTBridge with config and baudrate
    bridge.start()  # Call start method on bridge to begin MQTT and serial port processing loops
