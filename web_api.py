import asyncio  # Import asyncio library for asynchronous programming, event loops, and coroutines
import json  # Import json module to convert between JSON text and Python dictionaries
import sqlite3  # Import sqlite3 module to read historical data from local SQLite database file
import paho.mqtt.client as mqtt  # Import Eclipse Paho MQTT client library and alias it as 'mqtt'
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException  # Import web framework classes: app instance, WebSockets, disconnect handler, and HTTP exceptions
from fastapi.staticfiles import StaticFiles  # Import StaticFiles to serve HTML, CSS, JavaScript, and asset files from disk
from fastapi.responses import FileResponse  # Import FileResponse to send files directly over HTTP responses
from pydantic import BaseModel  # Import BaseModel from Pydantic for data validation and schema definition of API request bodies
from typing import List  # Import List type hint from typing module to annotate collections of objects
# ----------------------------------------------------------------------------------------------------------------------
# Configuration Constants
# ----------------------------------------------------------------------------------------------------------------------
MQTT_BROKER = "localhost"  # Network address of the MQTT message broker (localhost on the Raspberry Pi)
MQTT_PORT = 1883  # TCP network port for unencrypted MQTT traffic
DB_PATH = "sensor_data.db"  # File system path to the SQLite database containing historical sensor readings
# ----------------------------------------------------------------------------------------------------------------------
# FastAPI Application Instance
# ----------------------------------------------------------------------------------------------------------------------
app = FastAPI()  # Create the main FastAPI web application object that routes HTTP and WebSocket endpoints
app.mount("/static", StaticFiles(directory="static"), name="static")  # Mount the local folder 'static' to URL path '/static' to serve web UI files
# ----------------------------------------------------------------------------------------------------------------------
# WebSocket Connection Manager Class
# ----------------------------------------------------------------------------------------------------------------------
class ConnectionManager:  # Define a class to track and manage active browser WebSocket client connections
    def __init__(self):  # Constructor method that initializes a new ConnectionManager instance
        self.active_connections: List[WebSocket] = []  # Initialize empty list to store connected WebSocket instances
# ----------------------------------------------------------------------------------------------------------------------
    async def connect(self, websocket: WebSocket):  # Asynchronous coroutine to accept and register a new WebSocket connection
        await websocket.accept()  # Complete the WebSocket handshake protocol with the connecting browser client
        self.active_connections.append(websocket)  # Add the new client websocket object to the active connections list
# ----------------------------------------------------------------------------------------------------------------------
    def disconnect(self, websocket: WebSocket):  # Synchronous method to remove a closed WebSocket connection from the list
        if websocket in self.active_connections:  # Check if the given websocket object exists in the active connections list
            self.active_connections.remove(websocket)  # Remove the closed connection from the active list
# ----------------------------------------------------------------------------------------------------------------------
    async def broadcast(self, message: dict):  # Asynchronous coroutine to push a JSON message to all connected browsers
        for connection in self.active_connections:  # Iterate over each connected client in the active connections list
            try:  # Begin try block to send message without stopping if an individual client disconnects
                await connection.send_json(message)  # Send Python dictionary as a serialized JSON message over WebSocket
            except Exception:  # Catch any communication error (e.g. client unexpectedly closed browser tab)
                pass  # Ignore failure for this client and continue loop to send to remaining clients
# ----------------------------------------------------------------------------------------------------------------------
# Global Instances and Event Loop Reference
# ----------------------------------------------------------------------------------------------------------------------
manager = ConnectionManager()  # Instantiate the ConnectionManager class to manage browser WebSocket connections
mqtt_client = mqtt.Client()  # Instantiate Paho MQTT client to bridge MQTT messages to WebSocket clients
main_loop = None  # Global variable holding reference to the main asyncio event loop running FastAPI
# ----------------------------------------------------------------------------------------------------------------------
# MQTT Callback Functions
# ----------------------------------------------------------------------------------------------------------------------
def on_mqtt_connect(client, userdata, flags, rc):  # Callback invoked when WebAPI MQTT client finishes connecting to broker
    if rc == 0:  # Check if return code is 0, indicating successful connection
        print("WebAPI connected to MQTT Broker!")  # Print connection success to terminal
        client.subscribe("wsn/uplink/#")  # Subscribe to all sensor uplink messages using '#' multi-level wildcard
    else:  # Return code was non-zero, indicating connection error
        print(f"WebAPI failed to connect to MQTT, rc={rc}")  # Print failure message and error code to terminal
# ----------------------------------------------------------------------------------------------------------------------
def on_mqtt_message(client, userdata, msg):  # Callback executed by Paho MQTT thread whenever a new message is received
    try:  # Begin try block to safely handle JSON parsing and threadsafe forwarding
        payload = json.loads(msg.payload.decode())  # Decode byte payload to string and parse JSON into Python dictionary
        data = {  # Wrap topic and payload into a single dictionary to forward to frontend web clients
            "topic": msg.topic,  # The MQTT topic on which the message was published
            "payload": payload  # The parsed JSON message contents
        }  # Close data dictionary literal
        if main_loop and not main_loop.is_closed():  # Ensure main asyncio event loop is running and accessible
            asyncio.run_coroutine_threadsafe(manager.broadcast(data), main_loop)  # Schedule the async broadcast coroutine from the MQTT thread into asyncio loop
    except Exception as e:  # Catch any exception during message decoding or scheduling
        print(f"Error handling MQTT msg: {e}")  # Print error details to terminal
# ----------------------------------------------------------------------------------------------------------------------
mqtt_client.on_connect = on_mqtt_connect  # Assign the on_connect callback function to the MQTT client instance
mqtt_client.on_message = on_mqtt_message  # Assign the on_message callback function to the MQTT client instance
# ----------------------------------------------------------------------------------------------------------------------
# Application Lifecycle Event Handlers
# ----------------------------------------------------------------------------------------------------------------------
@app.on_event("startup")  # Decorator instructing FastAPI to execute this function when web server starts up
async def startup_event():  # Asynchronous startup lifecycle function
    global main_loop  # Declare intent to modify the global variable 'main_loop'
    main_loop = asyncio.get_running_loop()  # Capture reference to the current running asyncio event loop
    mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)  # Connect to MQTT broker with 60s keepalive interval
    mqtt_client.loop_start()  # Launch Paho's internal background thread to handle MQTT network operations
# ----------------------------------------------------------------------------------------------------------------------
@app.on_event("shutdown")  # Decorator instructing FastAPI to execute this function when web server shuts down
async def shutdown_event():  # Asynchronous shutdown lifecycle function
    mqtt_client.loop_stop()  # Stop Paho MQTT's background network processing thread
    mqtt_client.disconnect()  # Gracefully disconnect the MQTT client from the broker
# ----------------------------------------------------------------------------------------------------------------------
# Web Endpoints (HTTP Routes)
# ----------------------------------------------------------------------------------------------------------------------
@app.get("/")  # Decorator defining an HTTP GET endpoint at the root URL path '/'
async def read_index():  # Asynchronous route handler returning the frontend dashboard page
    return FileResponse("static/index.html")  # Return index.html file from static directory to browser client
# ----------------------------------------------------------------------------------------------------------------------
@app.get("/api/history")  # Decorator defining an HTTP GET endpoint at '/api/history' to fetch recent sensor readings
async def get_history(limit: int = 100):  # Asynchronous handler with optional query parameter 'limit' (default 100)
    try:  # Begin try block to execute database query safely
        conn = sqlite3.connect(DB_PATH)  # Open a connection to the SQLite database file
        conn.row_factory = sqlite3.Row  # Configure connection to return rows as dictionary-like Row objects
        cursor = conn.cursor()  # Create a cursor to execute SQL commands
        cursor.execute("SELECT * FROM sensor_readings ORDER BY timestamp_ms DESC LIMIT ?", (limit,))  # Execute SQL query to fetch newest records up to limit
        rows = cursor.fetchall()  # Fetch all matching rows returned by the query into a list
        conn.close()  # Close the SQLite database connection
        return [dict(row) for row in rows]  # Convert each sqlite3.Row object into a standard Python dict and return list as JSON response
    except Exception as e:  # Catch any database or query exception
        raise HTTPException(status_code=500, detail=str(e))  # Raise HTTP 500 Internal Server Error returning error message to client
# ----------------------------------------------------------------------------------------------------------------------
@app.get("/api/history/{node_id}")  # Decorator defining HTTP GET endpoint at '/api/history/{node_id}' with path parameter
async def get_node_history(node_id: str, limit: int = 50):  # Handler receiving URL path param 'node_id' and query param 'limit' (default 50)
    try:  # Begin try block to execute database query safely
        conn = sqlite3.connect(DB_PATH)  # Open connection to the SQLite database file
        conn.row_factory = sqlite3.Row  # Configure rows to be accessed by column names
        cursor = conn.cursor()  # Create database cursor object
        cursor.execute("SELECT * FROM sensor_readings WHERE node_id=? ORDER BY timestamp_ms DESC LIMIT ?", (node_id, limit))  # Query readings filtered by node_id
        rows = cursor.fetchall()  # Retrieve all matching rows from query execution
        conn.close()  # Close the SQLite database connection
        return [dict(row) for row in rows]  # Convert each row to dictionary and return as JSON array
    except Exception as e:  # Catch any database or query exception
        raise HTTPException(status_code=500, detail=str(e))  # Return HTTP 500 Internal Server Error response with exception details
# ----------------------------------------------------------------------------------------------------------------------
# Pydantic Model for Command Payloads
# ----------------------------------------------------------------------------------------------------------------------
class CommandPayload(BaseModel):  # Define Pydantic schema model for validating incoming JSON POST request body
    target_addr: str  # Destination node hardware address as string (e.g., '0x0001')
    cmd_type: int  # Command opcode integer (e.g. 2 for ping, 3 for interval change)
    proto: int  # Protocol index integer (0: WiFi, 1: Zigbee, 2: LoRa)
    val1: int = 0  # Optional integer parameter 1, default value 0
    val2: int = 0  # Optional integer parameter 2, default value 0
    val3: int = 0  # Optional integer parameter 3, default value 0
# ----------------------------------------------------------------------------------------------------------------------
@app.post("/api/command")  # Decorator defining an HTTP POST endpoint at '/api/command' to issue downlink commands
async def send_command(cmd: CommandPayload):  # Asynchronous handler taking validated CommandPayload request body
    try:  # Begin try block to handle command publication
        payload = cmd.dict()  # Convert Pydantic data model instance into a standard Python dictionary
        mqtt_client.publish("wsn/downlink/command", json.dumps(payload))  # Convert dict to JSON string and publish to downlink MQTT topic
        return {"status": "success", "message": "Command published to MQTT"}  # Return success response JSON to web client
    except Exception as e:  # Catch any publication or connection error
        raise HTTPException(status_code=500, detail=str(e))  # Raise HTTP 500 status code with error message
# ----------------------------------------------------------------------------------------------------------------------
# WebSocket Live Streaming Endpoint
# ----------------------------------------------------------------------------------------------------------------------
@app.websocket("/ws/live")  # Decorator defining a real-time WebSocket endpoint at '/ws/live'
async def websocket_endpoint(websocket: WebSocket):  # Asynchronous WebSocket handler function
    await manager.connect(websocket)  # Accept the incoming client connection and register it in ConnectionManager
    try:  # Begin try block to keep connection open and listen for client messages
        while True:  # Run an infinite loop waiting for messages from the connected browser
            data = await websocket.receive_text()  # Asynchronously wait for incoming text/heartbeat from the client
    except WebSocketDisconnect:  # Catch exception raised when the browser client closes or disconnects the WebSocket
        manager.disconnect(websocket)  # Remove the disconnected websocket client from active connections list
