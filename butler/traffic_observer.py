import paho.mqtt.client as mqtt
import json
import sys

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        client.subscribe("butler/#")
    else:
        print(f"Failed to connect, return code {rc}")
        sys.exit(1)

def on_message(client, userdata, msg):
    print(f"\nTopic: {msg.topic}")
    try:
        data = json.loads(msg.payload)
        print(json.dumps(data, indent=2))
    except json.JSONDecodeError:
        print(f"Raw: {msg.payload.decode(errors='replace')}")
    print("-" * 30)

def main():
    client = mqtt.Client()
    client.on_connect = on_connect
    client.on_message = on_message

    try:
        client.connect("localhost", 1883, 60)
        client.loop_forever()
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
