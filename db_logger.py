import sqlite3
import json
import time
import sys
import paho.mqtt.client as mqtt

MQTT_BROKER = "localhost"
MQTT_PORT = 1883
DB_PATH = "sensor_data.db"

class DatabaseLogger:
    def __init__(self):
        # Connect to SQLite (this will create the file if it doesn't exist)
        self.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        self.setup_tables()
        
        # Setup MQTT
        self.mqtt_client = mqtt.Client()
        self.mqtt_client.on_connect = self.on_mqtt_connect
        self.mqtt_client.on_message = self.on_mqtt_message

    def setup_tables(self):
        cursor = self.conn.cursor()
        
        # Sensor Data Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sensor_readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp_ms INTEGER,
                node_id TEXT,
                protocol TEXT,
                rssi INTEGER,
                batt_mv INTEGER,
                temp_c REAL,
                gnss_sats INTEGER,
                lat REAL,
                lon REAL,
                accel_x INTEGER,
                accel_y INTEGER,
                accel_z INTEGER
            )
        """)
        
        # Node Status Table (Useful for tracking address changes when switching to Zigbee)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS node_status (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp_ms INTEGER,
                node_id TEXT,
                protocol TEXT,
                status TEXT
            )
        """)
        
        # Node Logs Table (For hardware text logs)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS node_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp_ms INTEGER,
                node_id TEXT,
                protocol TEXT,
                message TEXT
            )
        """)
        
        self.conn.commit()
        print("Database tables verified.")

    def start(self):
        print(f"Connecting to MQTT broker at {MQTT_BROKER}:{MQTT_PORT}...")
        try:
            self.mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
            self.mqtt_client.loop_forever()
        except Exception as e:
            print(f"Failed to connect to MQTT: {e}")
            sys.exit(1)
        except KeyboardInterrupt:
            print("\nShutting down logger...")
            self.conn.close()

    def on_mqtt_connect(self, client, userdata, flags, rc):
        if rc == 0:
            print("Connected to MQTT Broker!")
            # Subscribe to all uplink topics
            client.subscribe("wsn/uplink/#")
            print("Subscribed to wsn/uplink/#")
        else:
            print(f"Failed to connect to MQTT, return code {rc}")

    def on_mqtt_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
            topic = msg.topic
            
            cursor = self.conn.cursor()
            now_ms = int(time.time() * 1000)
            
            if topic == "wsn/uplink/data":
                cursor.execute("""
                    INSERT INTO sensor_readings 
                    (timestamp_ms, node_id, protocol, rssi, batt_mv, temp_c, gnss_sats, lat, lon, accel_x, accel_y, accel_z)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    payload.get("timestamp_ms", now_ms),
                    payload.get("node_id", "UNKNOWN"),
                    payload.get("protocol", "UNKNOWN"),
                    payload.get("rssi", 0),
                    payload.get("batt_mv", 0),
                    payload.get("temp_c", 0.0),
                    payload.get("gnss_sats", 0),
                    payload.get("lat", 0.0),
                    payload.get("lon", 0.0),
                    payload.get("accel_x", 0),
                    payload.get("accel_y", 0),
                    payload.get("accel_z", 0)
                ))
                print(f"Logged DATA from {payload.get('node_id', 'UNKNOWN')} via {payload.get('protocol', 'UNKNOWN')}")
                
            elif topic == "wsn/uplink/status":
                cursor.execute("""
                    INSERT INTO node_status (timestamp_ms, node_id, protocol, status)
                    VALUES (?, ?, ?, ?)
                """, (
                    now_ms,
                    payload.get("node_id", "UNKNOWN"),
                    payload.get("protocol", "UNKNOWN"),
                    payload.get("status", "UNKNOWN")
                ))
                print(f"Logged STATUS from {payload.get('node_id', 'UNKNOWN')} via {payload.get('protocol', 'UNKNOWN')}")
                
            elif topic == "wsn/uplink/log":
                cursor.execute("""
                    INSERT INTO node_logs (timestamp_ms, node_id, protocol, message)
                    VALUES (?, ?, ?, ?)
                """, (
                    now_ms,
                    payload.get("node_id", "UNKNOWN"),  # Raw serial logs might not have a node_id
                    payload.get("protocol", "UNKNOWN"),
                    payload.get("message", "")
                ))
                print(f"Logged MSG via {payload.get('protocol', 'UNKNOWN')}")

            self.conn.commit()

        except json.JSONDecodeError:
            print(f"Invalid JSON received on {msg.topic}")
        except sqlite3.Error as e:
            print(f"Database error: {e}")
        except Exception as e:
            print(f"Error processing MQTT message: {e}")

if __name__ == "__main__":
    logger = DatabaseLogger()
    logger.start()
