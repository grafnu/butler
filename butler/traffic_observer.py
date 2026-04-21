import json
import paho.mqtt.client as mqtt
from datetime import datetime

def on_message(client, userdata, msg):
    try:
        data = json.loads(msg.payload.decode())
        print(f"--- {datetime.now().isoformat()} ---")
        print(f"Topic: {msg.topic}")
        print(json.dumps(data, indent=4))
        print("-" * 40)
    except Exception as e:
        print(f"Error on {msg.topic}: {e}")

def main():
    # paho-mqtt 2.0+ requires callback_api_version
    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, "observer")
    except AttributeError:
        # Fallback for older paho-mqtt versions
        client = mqtt.Client("observer")
        
    client.on_message = on_message
    client.connect("localhost", 1883, 60)
    client.subscribe("butler/#")
    print("Traffic Observer started. Listening on 'butler/#'...")
    client.loop_forever()

if __name__ == "__main__":
    main()
