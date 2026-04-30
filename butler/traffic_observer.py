import json
import paho.mqtt.client as mqtt
import os

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("[observer] Connected to MQTT broker")
        client.subscribe("#")
    else:
        print(f"[observer] Connection failed with code {rc}")

def on_message(client, userdata, msg):
    try:
        # Unbuffered output: ensure stdout is flushed
        import sys
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
        import sys
        print(f"Error processing message on {msg.topic}: {e}")
        sys.stdout.flush()

def main():
    host = os.environ.get("MQTT_HOST", "localhost")
    port = int(os.environ.get("MQTT_PORT", 1883))
    
    # Connectivity Parameters: Sufficient to diagnose communication substrate
    print(f"[observer] Starting Observer...")
    print(f"MQTT Host: {host}")
    print(f"MQTT Port: {port}")
    
    # Try to support both paho-mqtt 1.x and 2.x
    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
    except AttributeError:
        client = mqtt.Client()
        
    client.on_connect = on_connect
    client.on_message = on_message
    
    print(f"[observer] Connecting to {host}:{port}...")
    client.connect(host, port, 60)
    client.loop_forever()

if __name__ == "__main__":
    main()
