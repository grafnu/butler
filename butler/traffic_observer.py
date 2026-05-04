import json
import paho.mqtt.client as mqtt
import os
import argparse
import sys
from butler.common import parse_conn_spec

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("[observer] Connected to MQTT broker")
        # Subscribe to all traffic based on prefix if available
        prefix = userdata.get("prefix")
        if prefix:
            client.subscribe(f"/{prefix}/#")
        else:
            client.subscribe("#")
    else:
        print(f"[observer] Connection failed with code {rc}")

def on_message(client, userdata, msg):
    try:
        # Unbuffered output: ensure stdout is flushed
        payload = msg.payload.decode()
        try:
            data = json.loads(payload)
            # Output on one line, including complete message payload
            payload_str = json.dumps(data)
        except json.JSONDecodeError:
            # Protocol Decoupling & Graceful Degradation: Show raw if not JSON
            payload_str = payload
        
        print(f"{msg.topic}: {payload_str}")
        sys.stdout.flush()
    except Exception as e:
        print(f"Error processing message on {msg.topic}: {e}")
        sys.stdout.flush()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("conn_spec", nargs="?", help="Connection specification")
    args = parser.parse_args()

    user, host, port, prefix = parse_conn_spec(args.conn_spec)
    
    # Connectivity Parameters: Sufficient to diagnose communication substrate
    print(f"[observer] Starting Observer...")
    print(f"MQTT Host: {host}")
    print(f"MQTT Port: {port}")
    if prefix:
        print(f"Topic Prefix: {prefix}")
    
    # Try to support both paho-mqtt 1.x and 2.x
    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, userdata={"prefix": prefix})
    except AttributeError:
        client = mqtt.Client(userdata={"prefix": prefix})
        
    client.on_connect = on_connect
    client.on_message = on_message
    
    print(f"[observer] Connecting to {host}:{port}...")
    client.connect(host, port, 60)
    client.loop_forever()

if __name__ == "__main__":
    main()
