import paho.mqtt.client as mqtt
import json
import time
import sys
import datetime
import re
from butler.messaging import create_message, parse_message

class Verifier:
    def __init__(self):
        self.client = mqtt.Client()
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        self.sequences = {} # device_id: [states seen]
        self.timestamp_regex = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$')

    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            client.subscribe("butler/#")
        else:
            print(f"Verifier failed to connect: {rc}")

    def on_message(self, client, userdata, msg):
        message = parse_message(msg.payload)
        if not message:
            return

        timestamp = message.get("timestamp")
        if not timestamp or not self.timestamp_regex.match(timestamp):
            self.report_error(f"Invalid timestamp format in message: {timestamp}")
            return

        # Topic: butler/devices/{device_id}/{subType}/{subFolder}
        parts = msg.topic.split('/')
        if len(parts) < 4:
            return
        
        device_id = parts[2]
        sub_type = parts[3]
        sub_folder = parts[4] if len(parts) > 4 else None

        if sub_type == "config" and sub_folder == "system":
            self.sequences[device_id] = ["update_sent"]
            print(f"[verifier] Detected update start for {device_id}")
        
        elif sub_type == "state" and sub_folder == "system":
            system = message.get("system", {})
            state = system.get("state")
            if device_id in self.sequences:
                if state not in self.sequences[device_id]:
                    self.sequences[device_id].append(state)
                
                if state == "success":
                    if "pending" in self.sequences[device_id]:
                        self.report_success(device_id, "Update sequence completed successfully")
                    else:
                        self.report_error(f"Device {device_id} reported success without pending state")
                    del self.sequences[device_id]
                elif state == "failure":
                    self.report_info(device_id, "Device reported failure state")
                    del self.sequences[device_id]

    def report_success(self, device_id, text):
        payload = {"result": "PASS", "device_id": device_id, "message": text}
        msg = create_message(subfolder="validation", payload=payload)
        self.client.publish("butler/verify", json.dumps(msg))
        print(f"[verifier] SUCCESS: {device_id} - {text}")

    def report_error(self, text):
        payload = {"result": "FAIL", "message": text}
        msg = create_message(subfolder="validation", payload=payload)
        self.client.publish("butler/verify", json.dumps(msg))
        print(f"[verifier] ERROR: {text}")

    def report_info(self, device_id, text):
        payload = {"result": "INFO", "device_id": device_id, "message": text}
        msg = create_message(subfolder="validation", payload=payload)
        self.client.publish("butler/verify", json.dumps(msg))
        print(f"[verifier] INFO: {device_id} - {text}")

    def run(self):
        self.client.connect("localhost", 1883, 60)
        self.client.loop_forever()

def main():
    verifier = Verifier()
    print("Starting Verifier Watcher...")
    verifier.run()

if __name__ == "__main__":
    main()
