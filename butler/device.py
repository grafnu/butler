import paho.mqtt.client as mqtt
import json
import time
import sys
import os
import hashlib
import argparse
from butler.messaging import create_message, parse_message

class MockDevice:
    def __init__(self, device_id, fail_mode=False):
        self.device_id = device_id
        self.fail_mode = fail_mode
        self.current_version = "0.0.0" 
        self.state = "quiescent"
        self.client = mqtt.Client()
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        self.subsystem = "main"
        self.seen_nonces = set()

    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            client.subscribe(f"butler/{self.device_id}/update_payload")
            # Initial status report
            self.report_status()
        else:
            print(f"Device {self.device_id} failed to connect: {rc}")

    def on_message(self, client, userdata, msg):
        message = parse_message(msg.payload)
        if not message or message.get("type") != "update_payload":
            return

        nonce = message.get("nonce")
        if nonce in self.seen_nonces:
            return
        self.seen_nonces.add(nonce)
        if len(self.seen_nonces) > 1000:
            self.seen_nonces.clear()

        payload = message.get("payload", {})
        url = payload.get("url")
        sha256 = payload.get("sha256")
        version = payload.get("version")
        
        print(f"[mockit] Device {self.device_id} received update to {version}")
        
        # Transition to pending
        self.state = "pending"
        self.report_status()
        
        if self.fail_mode:
            print(f"[mockit] FAILURE MODE: Not progressing from pending.")
            # We stay in pending and never report success/failure
            return

        # Simulate download and verify
        time.sleep(1)
        
        try:
            if not os.path.exists(url):
                print(f"[mockit] Blob not found at {url}")
                self.state = "failure"
                self.report_status()
                return
            
            with open(url, "rb") as f:
                data = f.read()
                actual_hash = hashlib.sha256(data).hexdigest()
            
            if actual_hash == sha256:
                print(f"[mockit] Verification success. Applying...")
                time.sleep(1)
                self.current_version = version
                self.state = "success"
                self.report_status()
                # After reporting success, move back to quiescent
                self.state = "quiescent"
                # Success is a transient state in terms of reporting? 
                # The spec says: "Report success (if verified) or failure".
                # Then it says: "Report Status: Periodically publish current version and state (quiescent)".
            else:
                print(f"[mockit] Hash mismatch! Expected {sha256}, got {actual_hash}")
                self.state = "failure"
                self.report_status()
        except Exception as e:
            print(f"[mockit] Error applying update: {e}")
            self.state = "failure"
            self.report_status()

    def report_status(self):
        payload = {
            "current_version": self.current_version,
            "state": self.state,
            "subsystem": self.subsystem
        }
        msg = create_message(source="mockit", destination="butler", msg_type="status", payload=payload)
        self.client.publish(f"butler/{self.device_id}/status", json.dumps(msg))

    def run(self):
        self.client.connect("localhost", 1883, 60)
        self.client.loop_start()
        
        try:
            while True:
                self.report_status()
                time.sleep(10) # Periodic report
        except KeyboardInterrupt:
            self.client.loop_stop()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("device_id")
    parser.add_argument("-f", action="store_true", help="Enable failure mode")
    args = parser.parse_args()

    device = MockDevice(args.device_id, fail_mode=args.f)
    print(f"Starting mock device {args.device_id}...")
    device.run()

if __name__ == "__main__":
    main()
