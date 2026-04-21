import json
import shutil
import paho.mqtt.client as mqtt
from datetime import datetime

def on_message(client, userdata, msg):
    try:
        data = json.loads(msg.payload.decode())
        ts = datetime.now().strftime("%H:%M:%S")
        json_str = json.dumps(data)
        line = f"{ts} | {msg.topic: <30} | {json_str}"
        
        # Truncate to window size
        columns, _ = shutil.get_terminal_size(fallback=(120, 24))
        if len(line) > columns:
            line = line[:columns-3] + "..."
            
        print(line, flush=True)
    except Exception as e:
        print(f"Error on {msg.topic}: {e}")

def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        client.subscribe("butler/#")
    else:
        print(f"Failed to connect, return code {rc}")

def main():
    # paho-mqtt 2.0+ requires callback_api_version
    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, "observer")
    except AttributeError:
        # Fallback for older paho-mqtt versions
        client = mqtt.Client("observer")
        
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect("localhost", 1883, 60)
    print("Traffic Observer started. Listening on 'butler/#'...")
    client.loop_forever()

if __name__ == "__main__":
    main()
