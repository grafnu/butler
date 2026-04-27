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
            # We don't necessarily want to spam errors for every random message on the bus
            # but the spec says "If any timestamp does not conform then validator should reject the message as invalid."
            # Wait, "validator" or "verifier"? Spec says "validator should reject". Verifier IS the validator.
            return

        timestamp = message.get("timestamp")
        if not timestamp or not self.timestamp_regex.match(timestamp):
            self.report_error(f"Invalid timestamp format in message: {timestamp}")
            return

        source = message.get("source")
        msg_type = message.get("type")
        device_id = None
        
        parts = msg.topic.split('/')
        if len(parts) >= 2:
            device_id = parts[1]

        if msg_type == "update_payload" and source == "butler":
            self.sequences[device_id] = ["update_sent"]
            print(f"[verifier] Detected update start for {device_id}")
        
        elif msg_type == "status" and source == "mockit":
            state = message.get("payload", {}).get("state")
            if device_id in self.sequences:
                self.sequences[device_id].append(state)
                
                if state == "success":
                    if "pending" in self.sequences[device_id]:
                        self.report_success(device_id, "Update sequence completed successfully")
                    else:
                        self.report_error(f"Device {device_id} reported success without pending state")
                    del self.sequences[device_id]
                elif state == "failure":
                    # Check if it was a rollback or an expected failure
                    self.report_info(device_id, "Device reported failure state")
                    # We might not delete the sequence yet if we expect a rollback?
                    # But the spec says "When it detects a valid sequence... it will output a validation message."
                    del self.sequences[device_id]

    def report_success(self, device_id, text):
        payload = {"result": "PASS", "device_id": device_id, "message": text}
        msg = create_message(source="verifier", destination="all", msg_type="verify", payload=payload)
        self.client.publish(f"butler/{device_id}/verify", json.dumps(msg))
        print(f"[verifier] SUCCESS: {device_id} - {text}")

    def report_error(self, text):
        payload = {"result": "FAIL", "message": text}
        msg = create_message(source="verifier", destination="all", msg_type="verify", payload=payload)
        self.client.publish("butler/verify", json.dumps(msg))
        print(f"[verifier] ERROR: {text}")

    def report_info(self, device_id, text):
        payload = {"result": "INFO", "device_id": device_id, "message": text}
        msg = create_message(source="verifier", destination="all", msg_type="verify", payload=payload)
        self.client.publish(f"butler/{device_id}/verify", json.dumps(msg))
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
