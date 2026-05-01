import paho.mqtt.client as mqtt
import json
import time
import sys
import re
import os
from butler.messaging import create_uufi_message, parse_uufi_message

class Verifier:
    def __init__(self):
        self.client = mqtt.Client()
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        self.sequences = {} # device_id: [states seen]
        self.seen_nonces = set()
        self.timestamp_regex = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$')
        self.registry_id = os.environ.get("BUTLER_REGISTRY_ID", "butler-registry")
        self.project_id = os.environ.get("BUTLER_PROJECT_ID", "butler-project")
        self.active_clients = set()

    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            client.subscribe("/uufi/#")
        else:
            print(f"Verifier failed to connect: {rc}", flush=True)

    def on_message(self, client, userdata, msg):
        message, envelope = parse_uufi_message(msg.payload)
        if not message:
            return

        # Mandatory field validation
        if "timestamp" not in message or "version" not in message:
            self.report_error("Missing mandatory UDMI fields (timestamp or version)")
            return
        
        timestamp = message.get("timestamp")
        if not self.timestamp_regex.match(timestamp):
            self.report_error(f"Invalid timestamp format in message: {timestamp}")
            return

        nonce = message.get("nonce")
        if nonce in self.seen_nonces:
            return
        self.seen_nonces.add(nonce)
        if len(self.seen_nonces) > 1000:
            self.seen_nonces.clear()

        parts = msg.topic.split('/')
        if len(parts) < 5:
            return

        # Handshake awareness
        if parts[2] == 'c':
            source = parts[3]
            sub_type = parts[4]
            sub_folder = parts[5]
            if sub_type == "config" and sub_folder == "udmi":
                udmi = message.get("udmi", {})
                if "reply" in udmi:
                    self.active_clients.add(source)
                    self.report_info(source, f"Client {source} successfully activated via handshake")
            return

        if len(parts) < 8:
            return
        
        registry_id = parts[2]
        device_id = parts[5]
        sub_type = parts[6]
        sub_folder = parts[7]

        # Update registry_id if not set or detected from traffic
        if not os.environ.get("BUTLER_REGISTRY_ID") and registry_id != self.registry_id:
            self.registry_id = registry_id

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
        print(f"[verifier] SUCCESS: {device_id} - {text}", flush=True)

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
        print(f"[verifier] ERROR: {text}", flush=True)

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
        print(f"[verifier] INFO: {device_id} - {text}", flush=True)

    def run(self):
        host = "localhost"
        port = 1883
        print(f"Verifier connecting to MQTT broker at {host}:{port}", flush=True)
        self.client.connect(host, port, 60)
        self.client.loop_forever()

def main():
    verifier = Verifier()
    print("Starting Verifier Watcher...")
    verifier.run()

if __name__ == "__main__":
    main()
