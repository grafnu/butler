import time
import json
import hashlib
import argparse
import os
import urllib.parse
from butler.common import ButlerMQTTBase
from butler.model_repo import ModelRepository

class MocketDevice(ButlerMQTTBase):
    def __init__(self, device_id, failure_mode=False):
        super().__init__(source="mockit")
        self.device_id = device_id
        self.failure_mode = failure_mode
        self.state = "quiescent"
        
        # Try to load current version from model
        model_repo = ModelRepository()
        device_state = model_repo.get_device_state(device_id)
        if device_state:
            self.current_version = device_state.get("current_version", "0.0.0")
        else:
            self.current_version = "0.0.0"

    def on_connect(self):
        print(f"Device {self.device_id} connected to bus.")
        self.subscribe(f"devices/{self.device_id}/config")
        self.report_status()

    def on_message(self, topic, data):
        if topic == f"devices/{self.device_id}/config":
            self.handle_update(data)

    def handle_update(self, data):
        payload = data.get("payload", {})
        target_version = payload.get("version")
        url = payload.get("url")
        expected_sha = payload.get("sha256")

        print(f"Device {self.device_id} received update payload for version {target_version}")
        
        self.state = "pending"
        self.report_status()

        if self.failure_mode:
            print(f"Device {self.device_id} in failure mode. Simulating failure...")
            time.sleep(2)
            self.state = "failure"
            self.report_status()
            # In failure mode, we might just stay in failure or return to quiescent
            # but reporting failure once is enough for the orchestrator to trigger rollback.
            time.sleep(1)
            self.state = "quiescent"
            return

        # Simulate download and verification
        time.sleep(2)
        try:
            parsed_url = urllib.parse.urlparse(url)
            if parsed_url.scheme == 'file':
                file_path = parsed_url.path
                if os.path.exists(file_path):
                    with open(file_path, 'rb') as f:
                        content = f.read()
                        actual_sha = hashlib.sha256(content).hexdigest()
                    
                    if actual_sha == expected_sha:
                        print(f"Device {self.device_id} verification success.")
                        self.current_version = target_version
                        self.state = "success"
                    else:
                        print(f"Device {self.device_id} verification failed: SHA mismatch.")
                        self.state = "failure"
                else:
                    print(f"Device {self.device_id} error: Blob file not found at {file_path}")
                    self.state = "failure"
            else:
                print(f"Device {self.device_id} error: Unsupported URL scheme {parsed_url.scheme}")
                self.state = "failure"
        except Exception as e:
            print(f"Device {self.device_id} error during update: {e}")
            self.state = "failure"

        self.report_status()
        
        # After success or failure, transition back to quiescent for next reports
        if self.state == "success":
            time.sleep(1) # Let the success message be seen
            self.state = "quiescent"
        elif self.state == "failure":
            time.sleep(1)
            self.state = "quiescent"

    def report_status(self):
        payload = {
            "version": self.current_version,
            "state": self.state
        }
        self.publish(self.device_id, "state", payload)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("device_id", help="Device ID")
    parser.add_argument("-f", "--failure", action="store_true", help="Enable failure mode")
    args = parser.parse_args()

    device = MocketDevice(args.device_id, failure_mode=args.failure)
    device.connect()
    device.loop_start()

    print(f"Device {args.device_id} running...")
    try:
        while True:
            device.report_status()
            time.sleep(10)
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
