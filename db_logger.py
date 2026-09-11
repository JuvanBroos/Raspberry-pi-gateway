import sqlite3  # Import Python's built-in sqlite3 module to manage and query local SQLite databases
import json  # Import the json library to parse incoming JSON strings into Python dictionaries and vice-versa
import time  # Import the time module to work with system clocks, timestamps, and execution delays
import sys  # Import the sys module to interact with system-specific parameters and exit the script when needed
import paho.mqtt.client as mqtt  # Import Eclipse Paho MQTT client class and alias it as 'mqtt' for convenience
# ----------------------------------------------------------------------------------------------------------------------
# Configuration Constants
# ----------------------------------------------------------------------------------------------------------------------
MQTT_BROKER = "localhost"  # Set the MQTT broker network hostname/IP (pointing to localhost on the Raspberry Pi)
MQTT_PORT = 1883  # Define the standard unencrypted port number (1883) used by the MQTT broker
DB_PATH = "sensor_data.db"  # Specify the file system path/name for the SQLite database file
# ----------------------------------------------------------------------------------------------------------------------
# DatabaseLogger Class Definition
# ----------------------------------------------------------------------------------------------------------------------
class DatabaseLogger:  # Define a class named DatabaseLogger to encapsulate all database logging and MQTT behaviors
    def __init__(self):  # Define the class constructor method; runs automatically whenever a new instance is created
        self.conn = sqlite3.connect(DB_PATH, check_same_thread=False)  # Connect to the SQLite file; check_same_thread=False allows cross-thread access
        self.setup_tables()  # Call the internal setup_tables method to ensure necessary database tables exist
        self.mqtt_client = mqtt.Client()  # Instantiate an MQTT client object and store it as an attribute of this instance
        self.mqtt_client.on_connect = self.on_mqtt_connect  # Register the on_mqtt_connect callback function for when MQTT connection is established
        self.mqtt_client.on_message = self.on_mqtt_message  # Register the on_mqtt_message callback function for when new MQTT messages arrive
# ----------------------------------------------------------------------------------------------------------------------
    def setup_tables(self):  # Define method to initialize SQLite database tables for sensor readings, node status, and logs
        cursor = self.conn.cursor()  # Create a cursor object used to execute SQL statements and traverse database results
        cursor.execute(""" -- Execute multiline SQL statement to create the sensor_readings table
            CREATE TABLE IF NOT EXISTS sensor_readings ( -- Create table named sensor_readings if it doesn't already exist
                id INTEGER PRIMARY KEY AUTOINCREMENT, -- Unique auto-incrementing integer identifier for every row
                timestamp_ms INTEGER, -- Millisecond Unix epoch timestamp representing when the reading was taken
                node_id TEXT, -- Unique text identifier of the remote sensor node (e.g. '0x1234')
                protocol TEXT, -- Communication protocol used by node: 'WiFi/ESP-NOW', 'Zigbee', or 'LoRa'
                rssi INTEGER, -- Received Signal Strength Indication in dBm measuring wireless signal power
                batt_mv INTEGER, -- Battery voltage level of the sensor node measured in millivolts
                temp_c REAL, -- Temperature value measured in degrees Celsius stored as a floating point number
                gnss_sats INTEGER, -- Total number of GPS/GNSS satellites currently tracked by the node
                lat REAL, -- Latitude GPS coordinate stored as a 64-bit floating point number
                lon REAL, -- Longitude GPS coordinate stored as a 64-bit floating point number
                accel_x REAL, -- Accelerometer sensor reading along the X-axis in Gs (gravity units)
                accel_y REAL, -- Accelerometer sensor reading along the Y-axis in Gs (gravity units)
                accel_z REAL -- Accelerometer sensor reading along the Z-axis in Gs (gravity units)
            ) -- Close the CREATE TABLE column definition block
        """)  # End of the SQL multiline string execution for sensor_readings
        cursor.execute(""" -- Execute multiline SQL statement to create the node_status table
            CREATE TABLE IF NOT EXISTS node_status ( -- Create table named node_status if it doesn't already exist
                id INTEGER PRIMARY KEY AUTOINCREMENT, -- Unique auto-incrementing integer identifier for each status event
                timestamp_ms INTEGER, -- Millisecond Unix epoch timestamp when this status update occurred
                node_id TEXT, -- Identifier of the remote node reporting its status
                protocol TEXT, -- Wireless protocol through which the status update was received
                status TEXT -- Text status description (such as 'Update', 'Online', or 'Sleep')
            ) -- Close the CREATE TABLE column definition block for node_status
        """)  # End of the SQL multiline string execution for node_status
        cursor.execute(""" -- Execute multiline SQL statement to create the node_logs table
            CREATE TABLE IF NOT EXISTS node_logs ( -- Create table named node_logs if it doesn't already exist
                id INTEGER PRIMARY KEY AUTOINCREMENT, -- Unique auto-incrementing integer identifier for each log entry
                timestamp_ms INTEGER, -- Millisecond Unix epoch timestamp when this log message was saved
                node_id TEXT, -- Identifier of the sensor node or gateway generating the log
                protocol TEXT, -- Wireless or serial protocol associated with this log message
                message TEXT -- The actual human-readable text log or debug string received
            ) -- Close the CREATE TABLE column definition block for node_logs
        """)  # End of the SQL multiline string execution for node_logs
        self.conn.commit()  # Save (commit) all pending database changes and table creations permanently to disk
        print("Database tables verified.")  # Print a confirmation message to the standard console output
# ----------------------------------------------------------------------------------------------------------------------
    def start(self):  # Define method to initiate the MQTT network connection and start the persistent listening loop
        print(f"Connecting to MQTT broker at {MQTT_BROKER}:{MQTT_PORT}...")  # Print formatted string showing target broker host and port
        try:  # Begin a try block to catch and handle any potential network or connection exceptions gracefully
            self.mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)  # Attempt connection to MQTT broker with a 60-second keepalive interval
            self.mqtt_client.loop_forever()  # Run a blocking network loop to handle reconnects, packet sending, and incoming callbacks
        except Exception as e:  # Catch any general exception that inherits from the base Exception class
            print(f"Failed to connect to MQTT: {e}")  # Print the error details to console using an f-string
            sys.exit(1)  # Terminate the Python process immediately returning an exit status code of 1 indicating failure
        except KeyboardInterrupt:  # Catch the KeyboardInterrupt exception raised when the user presses Ctrl+C in terminal
            print("\nShutting down logger...")  # Inform the user via console output that shutdown is in progress
            self.conn.close()  # Safely close the SQLite database connection to ensure no unwritten data is corrupted
# ----------------------------------------------------------------------------------------------------------------------
    def on_mqtt_connect(self, client, userdata, flags, rc):  # Define callback function invoked when MQTT client receives CONNACK from broker
        if rc == 0:  # Check if return code (rc) equals 0, meaning connection succeeded without errors
            print("Connected to MQTT Broker!")  # Display connection success message to the console
            client.subscribe("wsn/uplink/#")  # Subscribe to 'wsn/uplink/#' wildcard topic to receive all uplink subtopics
            print("Subscribed to wsn/uplink/#")  # Display subscription confirmation message to the console
        else:  # Handle cases where connection was rejected (e.g. invalid credentials or unavailable broker)
            print(f"Failed to connect to MQTT, return code {rc}")  # Print failure message containing the numeric MQTT error code
# ----------------------------------------------------------------------------------------------------------------------
    def on_mqtt_message(self, client, userdata, msg):  # Define callback function invoked when an MQTT message arrives on subscribed topic
        try:  # Begin try block to safely handle JSON parsing and SQLite insertion operations
            payload = json.loads(msg.payload.decode())  # Decode byte payload to UTF-8 string and parse into a Python dictionary
            topic = msg.topic  # Extract the MQTT topic string that this message was published to
            cursor = self.conn.cursor()  # Open a database cursor to perform database insertion operations
            now_ms = int(time.time() * 1000)  # Get current epoch timestamp in seconds, multiply by 1000, and convert to integer milliseconds
            if topic == "wsn/uplink/data":  # Check if the incoming message topic is the telemetry data topic
                cursor.execute(""" -- Execute SQL parameterized INSERT statement to store sensor readings safely
                    INSERT INTO sensor_readings -- Specify target table
                    (timestamp_ms, node_id, protocol, rssi, batt_mv, temp_c, gnss_sats, lat, lon, accel_x, accel_y, accel_z) -- Column names
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) -- Question mark parameter placeholders to prevent SQL injection
                """, (  # Supply tuple of values matching each question mark placeholder in the exact same order
                    payload.get("timestamp_ms", now_ms),  # Retrieve 'timestamp_ms' from dictionary, fallback to now_ms if absent
                    payload.get("node_id", "UNKNOWN"),  # Retrieve 'node_id', fallback to string "UNKNOWN" if key is missing
                    payload.get("protocol", "UNKNOWN"),  # Retrieve wireless 'protocol' string, fallback to "UNKNOWN"
                    payload.get("rssi", 0),  # Retrieve 'rssi' integer, fallback to 0 if key is missing
                    payload.get("batt_mv", 0),  # Retrieve battery millivolts 'batt_mv', fallback to 0 if key is missing
                    payload.get("temp_c", 0.0),  # Retrieve temperature 'temp_c', fallback to floating point 0.0 if missing
                    payload.get("gnss_sats", 0),  # Retrieve satellite count 'gnss_sats', fallback to 0 if key is missing
                    payload.get("lat", 0.0),  # Retrieve latitude 'lat', fallback to floating point 0.0 if missing
                    payload.get("lon", 0.0),  # Retrieve longitude 'lon', fallback to floating point 0.0 if missing
                    payload.get("accel_x", 0.0),  # Retrieve accelerometer X value, fallback to 0.0 if missing
                    payload.get("accel_y", 0.0),  # Retrieve accelerometer Y value, fallback to 0.0 if missing
                    payload.get("accel_z", 0.0)  # Retrieve accelerometer Z value, fallback to 0.0 if missing
                ))  # Close the tuple of values and the execute call
                print(f"Logged DATA from {payload.get('node_id', 'UNKNOWN')} via {payload.get('protocol', 'UNKNOWN')}")  # Print log confirmation
            elif topic == "wsn/uplink/status":  # Check if the incoming message topic is the node status notification topic
                cursor.execute(""" -- Execute SQL parameterized INSERT statement to store node status updates safely
                    INSERT INTO node_status (timestamp_ms, node_id, protocol, status) -- Specify target table and column names
                    VALUES (?, ?, ?, ?) -- Parameter placeholders for safe value binding
                """, (  # Supply tuple of values corresponding to the four column placeholders
                    now_ms,  # Current timestamp in milliseconds as an integer
                    payload.get("node_id", "UNKNOWN"),  # Identifier of the node, default to "UNKNOWN" if missing
                    payload.get("protocol", "UNKNOWN"),  # Protocol name used, default to "UNKNOWN" if missing
                    payload.get("status", "UNKNOWN")  # Status string (e.g. 'Update'), default to "UNKNOWN" if missing
                ))  # Close the parameter tuple and execute call
                print(f"Logged STATUS from {payload.get('node_id', 'UNKNOWN')} via {payload.get('protocol', 'UNKNOWN')}")  # Print confirmation
            elif topic == "wsn/uplink/log":  # Check if incoming message topic is the debug/hardware log topic
                cursor.execute(""" -- Execute SQL parameterized INSERT statement to store text log messages safely
                    INSERT INTO node_logs (timestamp_ms, node_id, protocol, message) -- Specify target table and column names
                    VALUES (?, ?, ?, ?) -- Parameter placeholders for safe value binding
                """, (  # Supply tuple of values corresponding to the four column placeholders
                    now_ms,  # Current timestamp in milliseconds as an integer
                    payload.get("node_id", "UNKNOWN"),  # Node identifier; raw serial logs might not have an ID so default to "UNKNOWN"
                    payload.get("protocol", "UNKNOWN"),  # Protocol name associated with the log, default to "UNKNOWN"
                    payload.get("message", "")  # The raw text log message string, default to empty string if missing
                ))  # Close the parameter tuple and execute call
                print(f"Logged MSG via {payload.get('protocol', 'UNKNOWN')}")  # Print confirmation of saved log message
            self.conn.commit()  # Permanently commit the database transaction to disk so the inserted data is saved
        except json.JSONDecodeError:  # Handle JSON parsing errors if incoming payload contains malformed or non-JSON text
            print(f"Invalid JSON received on {msg.topic}")  # Print warning indicating unparseable JSON arrived on this topic
        except sqlite3.Error as e:  # Handle SQLite database-specific errors (such as disk full or locked database)
            print(f"Database error: {e}")  # Print database error message to console
        except Exception as e:  # Catch any other unexpected exceptions occurring during message handling
            print(f"Error processing MQTT message: {e}")  # Print general error description to console
# ----------------------------------------------------------------------------------------------------------------------
# Script Entrypoint
# ----------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":  # Standard Python boilerplate: check if this file is run directly (not imported as a module)
    logger = DatabaseLogger()  # Create an instance of the DatabaseLogger class, triggering __init__ and DB table verification
    logger.start()  # Call the start method to establish the MQTT connection and begin receiving/logging messages forever
