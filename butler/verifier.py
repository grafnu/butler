import paho.mqtt.client as mqtt
import json
import time
import sys
import re
from butler.messaging import create_uufi_message, parse_uufi_message

class Verifier:
    def __init__(self):
        self.client = mqtt.Client()
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        self.sequences = {} # device_id: [states seen]
        self.seen_nonces = set()
        self.timestamp_regex = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$')
        self.registry_id = "butler-registry"

    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            client.subscribe("/uufi/#")
        else:
            print(f"Verifier failed to connect: {rc}")

    def on_message(self, client, userdata, msg):
        message, envelope = parse_uufi_message(msg.payload)
        if not message:
            return

        nonce = message.get("nonce")
        if nonce in self.seen_nonces:
            return
        self.seen_nonces.add(nonce)
        if len(self.seen_nonces) > 1000:
            self.seen_nonces.clear()

        timestamp = message.get("timestamp")
        if not timestamp or not self.timestamp_regex.match(timestamp):
            self.report_error(f"Invalid timestamp format in message: {timestamp}")
            return

        parts = msg.topic.split('/')
        if len(parts) < 8:
            return
        
        device_id = parts[5]
        sub_type = parts[6]
        sub_folder = parts[7]

        if sub_type == "config" and sub_folder == "system":
            if device_id in self.sequences:
                self.report_error(f"New update started for {device_id} before previous one finished")
            self.sequences[device_id] = ["update_sent"]
        
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
                    self.report_error(f"Device {device_id} reported failure state")
                    del self.sequences[device_id]

    def report_success(self, device_id, text):
        payload = {"result": "PASS", "device_id": device_id, "message": text}
        msg = create_uufi_message(
            registry_id=self.registry_id,
            device_id="butler",
            sub_type="events",
            sub_folder="verify",
            payload=payload,
            source="verifier"
        )
        topic = f"/uufi/r/{self.registry_id}/d/butler/events/verify"
        self.client.publish(topic, json.dumps(msg))
        print(f"[verifier] SUCCESS: {device_id} - {text}")

    def report_error(self, text):
        payload = {"result": "FAIL", "message": text}
        msg = create_uufi_message(
            registry_id=self.registry_id,
            device_id="butler",
            sub_type="events",
            sub_folder="verify",
            payload=payload,
            source="verifier"
        )
        topic = f"/uufi/r/{self.registry_id}/d/butler/events/verify"
        self.client.publish(topic, json.dumps(msg))
        print(f"[verifier] ERROR: {text}")

    def report_info(self, device_id, text):
        payload = {"result": "INFO", "device_id": device_id, "message": text}
        msg = create_uufi_message(
            registry_id=self.registry_id,
            device_id="butler",
            sub_type="events",
            sub_folder="verify",
            payload=payload,
            source="verifier"
        )
        topic = f"/uufi/r/{self.registry_id}/d/butler/events/verify"
        self.client.publish(topic, json.dumps(msg))
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
