import asyncio
import json
import sqlite3
import paho.mqtt.client as mqtt
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import List

MQTT_BROKER = "localhost"
MQTT_PORT = 1883
DB_PATH = "sensor_data.db"

app = FastAPI()

app.mount("/static", StaticFiles(directory="static"), name="static")

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                pass

manager = ConnectionManager()

mqtt_client = mqtt.Client()

main_loop = None

def on_mqtt_connect(client, userdata, flags, rc):
    if rc == 0:
        print("WebAPI connected to MQTT Broker!")
        client.subscribe("wsn/uplink/#")
    else:
        print(f"WebAPI failed to connect to MQTT, rc={rc}")

def on_mqtt_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode())
        data = {
            "topic": msg.topic,
            "payload": payload
        }
        if main_loop and not main_loop.is_closed():
            asyncio.run_coroutine_threadsafe(manager.broadcast(data), main_loop)
    except Exception as e:
        print(f"Error handling MQTT msg: {e}")

mqtt_client.on_connect = on_mqtt_connect
mqtt_client.on_message = on_mqtt_message

@app.on_event("startup")
async def startup_event():
    global main_loop
    main_loop = asyncio.get_running_loop()
    mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
    mqtt_client.loop_start()

@app.on_event("shutdown")
async def shutdown_event():
    mqtt_client.loop_stop()
    mqtt_client.disconnect()

@app.get("/")
async def read_index():
    return FileResponse("static/index.html")

@app.get("/api/history")
async def get_history(limit: int = 100):
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM sensor_readings ORDER BY timestamp_ms DESC LIMIT ?", (limit,))
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/history/{node_id}")
async def get_node_history(node_id: str, limit: int = 50):
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM sensor_readings WHERE node_id=? ORDER BY timestamp_ms DESC LIMIT ?", (node_id, limit))
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class CommandPayload(BaseModel):
    target_addr: str
    cmd_type: int
    proto: int
    val1: int = 0
    val2: int = 0
    val3: int = 0

@app.post("/api/command")
async def send_command(cmd: CommandPayload):
    try:
        payload = cmd.dict()
        mqtt_client.publish("wsn/downlink/command", json.dumps(payload))
        return {"status": "success", "message": "Command published to MQTT"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.websocket("/ws/live")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
