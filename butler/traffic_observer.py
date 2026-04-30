import paho.mqtt.client as mqtt
import json
import sys

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        client.subscribe("#")
    else:
        sys.exit(1)

def on_message(client, userdata, msg):
    try:
        data = json.loads(msg.payload)
        payload_str = json.dumps(data)
    except json.JSONDecodeError:
        payload_str = msg.payload.decode(errors='replace')
    
    print(f"Topic: {msg.topic} Payload: {payload_str}", flush=True)

def main():
    host = "localhost"
    port = 1883
    print(f"Observer connecting to MQTT broker at {host}:{port}", flush=True)
    client = mqtt.Client()
    client.on_connect = on_connect
    client.on_message = on_message

    try:
        client.connect(host, port, 60)
        client.loop_forever()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr, flush=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
