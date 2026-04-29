import json
import paho.mqtt.client as mqtt
import os

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("[observer] Connected to MQTT broker")
        client.subscribe("udmi/#")
        client.subscribe("butler/#") # Just in case
    else:
        print(f"[observer] Connection failed with code {rc}")

def on_message(client, userdata, msg):
    try:
        data = json.loads(msg.payload.decode())
        print(f"\n--- {msg.topic} ---")
        print(json.dumps(data, indent=2))
        # Flush to ensure output is visible immediately
        import sys
        sys.stdout.flush()
    except Exception as e:
        print(f"\n--- {msg.topic} (Raw) ---")
        print(msg.payload.decode())
        import sys
        sys.stdout.flush()

def main():
    host = os.environ.get("MQTT_HOST", "localhost")
    port = int(os.environ.get("MQTT_PORT", 1883))
    
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
