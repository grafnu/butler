import time
import hashlib
import requests
import os
from butler.common import ButlerMQTTClient

class DeviceConduit:
    def __init__(self, device_id, current_version="1.0.0", host="localhost", port=1883, failure_mode=False):
        self.device_id = device_id
        self.current_version = current_version
        self.state = "quiescent"
        self.failure_mode = failure_mode
        self.mqtt = ButlerMQTTClient("mockit", host, port)
        self.mqtt.set_on_message(self.handle_message)

    def start(self):
        self.mqtt.connect()
        self.mqtt.subscribe(f"butler/{self.device_id}/update_payload")
        print(f"Device {self.device_id} started at version {self.current_version}")
        if self.failure_mode:
            print(f"[mockit] FAILURE MODE ENABLED: Will ignore update payloads.")

    def stop(self):
        self.mqtt.disconnect()

    def report_status(self):
        payload = {
            "subsystem": "main",
            "current_version": self.current_version,
            "state": self.state
        }
        self.mqtt.publish(f"butler/{self.device_id}/status", "butler", "status", payload)

    def handle_message(self, topic, data):
        if data["type"] == "update_payload":
             if self.failure_mode:
                 print(f"[mockit] Ignoring update payload due to failure mode.")
                 return
             self.process_update(data["payload"])

    def process_update(self, payload):
        print(f"[mockit] Received update payload for version {payload['version']}")
        self.state = "pending"
        self.report_status()
        
        # Simulate download and verification
        time.sleep(1)
        url = payload["url"]
        expected_sha256 = payload["sha256"]
        
        try:
            # For simulation purposes, we just read the file if it's file://
            if url.startswith("file://"):
                path = url[len("file://"):]
                with open(path, "rb") as f:
                    content = f.read()
            else:
                 # In a real scenario, use requests.get(url)
                 print(f"[mockit] Simulating download from {url}")
                 content = b"simulated_content"
            
            actual_sha256 = hashlib.sha256(content).hexdigest()
            
            if actual_sha256 != expected_sha256:
                raise Exception("SHA256 mismatch")
            
            # Simulate apply
            print(f"[mockit] Applying update {payload['version']}")
            time.sleep(1)
            
            # Simulate a failure for version "9.9.9"
            if payload["version"] == "9.9.9":
                 raise Exception("Simulated installation failure")

            self.current_version = payload["version"]
            self.state = "success"
            print(f"[mockit] Update successful! Now at {self.current_version}")
            self.report_status()
            
            # Reset to quiescent after reporting success
            time.sleep(0.1)
            self.state = "quiescent"
            self.report_status()

        except Exception as e:
            print(f"[mockit] Update failed: {e}")
            self.state = "failure"
            self.report_status()
            
            # Reset to quiescent after reporting failure so we can receive next update
            time.sleep(0.1)
            self.state = "quiescent"
            self.report_status()

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Mock Device Conduit")
    parser.add_argument("device_id", help="ID of the device")
    parser.add_argument("-f", "--failure", action="store_true", help="Enable failure mode")
    args = parser.parse_args()

    device = DeviceConduit(args.device_id, failure_mode=args.failure)
    device.start()
    try:
        while True:
            device.report_status()
            time.sleep(5)
    except KeyboardInterrupt:
        device.stop()

if __name__ == "__main__":
    main()
