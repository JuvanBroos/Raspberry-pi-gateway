#!/bin/bash
cd /home/juvanbroos/Documents/gateway/Raspberry-pi-gateway
source venv/bin/activate

echo "Cleaning up any old background processes..."
pkill -f mqtt_bridge.py
pkill -f db_logger.py
pkill -f uvicorn
sleep 1

ARGS=""
if [ -e "/dev/esp32c6_lora_wifi_coord" ]; then
    ARGS="$ARGS --lora /dev/esp32c6_lora_wifi_coord --wifi /dev/esp32c6_lora_wifi_coord"
fi
if [ -e "/dev/esp32c6_zigbee_coord" ]; then
    ARGS="$ARGS --zigbee /dev/esp32c6_zigbee_coord"
fi

if [ -z "$ARGS" ]; then
    echo "WARNING: No gateway symlinks found! Falling back to /dev/ttyUSB0"
    ARGS="--lora /dev/ttyUSB0 --wifi /dev/ttyUSB0"
fi

echo "Starting MQTT Bridge with$ARGS ..."
python3 mqtt_bridge.py $ARGS &

echo "Starting Database Logger..."
python3 db_logger.py &

echo "Starting Web API (Dashboard)..."
uvicorn web_api:app --host 0.0.0.0 --port 8000
