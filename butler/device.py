import time
import argparse
import threading
import hashlib
import os
from butler.common import ButlerMQTTBase

class MocketDevice(ButlerMQTTBase):
    def __init__(self, device_id, failure_mode=False):
        super().__init__(source="mockit")
        self.device_id = device_id
        self.failure_mode = failure_mode
        self.current_version = "1.0"
        self.status = "quiescent"

    def on_connect(self):
        print(f"[mockit] Device connected: {self.device_id}")
        # Start handshake in a separate thread to allow retries
        threading.Thread(target=self.handshake_loop, daemon=True).start()
        # Subscribe to all config messages for this device
        self.subscribe_uufi()

    def handshake_loop(self):
        while not self.handshake_complete:
            print(f"[mockit] Attempting handshake for {self.device_id}...")
            self.start_handshake(device_id=self.device_id)
            # Wait for 5 seconds for a reply
            for _ in range(50):
                if self.handshake_complete:
                    return
                time.sleep(0.1)

    def on_message(self, topic, device_id, sub_type, sub_folder, data):
        if not self.handshake_complete:
            return

        if device_id != self.device_id:
            return

        # Handle Device Config (Target Device side)
        if sub_type == "config" and sub_folder == "update":
            self.handle_update_config(data)

    def handle_update_config(self, data):
        if self.status == "pending":
            return
        target_version = data.get("version")
        url = data.get("url")
        expected_sha256 = data.get("sha256")
        
        print(f"[mockit] Device {self.device_id} receiving update config to {target_version}")
        self.status = "pending"
        self.report_state()
        
        threading.Thread(target=self.apply_update, args=(target_version, url, expected_sha256), daemon=True).start()

    def apply_update(self, version, url, expected_sha256):
        time.sleep(2)
        if self.failure_mode:
            print(f"[mockit] Device {self.device_id} failing update (failure mode)")
            self.status = "failure"
        else:
            try:
                success = True
                if url and url.startswith("file://"):
                    path = url[7:]
                    if os.path.exists(path):
                        with open(path, 'rb') as f:
                            actual_sha256 = hashlib.sha256(f.read()).hexdigest()
                        if actual_sha256 != expected_sha256:
                            print(f"[mockit] Device {self.device_id} SHA256 mismatch: {actual_sha256} != {expected_sha256}")
                            success = False
                    else:
                        print(f"[mockit] Device {self.device_id} blob not found: {path}")
                        success = False
                
                if success:
                    print(f"[mockit] Device {self.device_id} update successful to {version}")
                    self.current_version = version
                    self.status = "success"
                else:
                    self.status = "failure"
            except Exception as e:
                print(f"[mockit] Device {self.device_id} error during update: {e}")
                self.status = "failure"
        
        self.report_state()
        time.sleep(1)
        self.status = "quiescent"
        self.report_state()

    def report_state(self):
        payload = {
            "version": self.current_version,
            "status": self.status
        }
        self.publish_uufi(self.device_id, "state", payload, "update", direction="reflect")

    def heartbeat(self):
        while True:
            if self.handshake_complete:
                self.report_state()
            time.sleep(30)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("device_id")
    parser.add_argument("-f", "--failure", action="store_true", help="Enable failure mode")
    args = parser.parse_args()
    
    device = MocketDevice(args.device_id, failure_mode=args.failure)
    device.connect()
    
    threading.Thread(target=device.heartbeat, daemon=True).start()
    
    device.loop_forever()

if __name__ == "__main__":
    main()
