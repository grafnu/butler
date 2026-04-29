import time
import argparse
import threading
import hashlib
from butler.common import ButlerMQTTBase

class ButlerDevice(ButlerMQTTBase):
    def __init__(self, device_id, failure_mode=False):
        super().__init__(source=f"device:{device_id}")
        self.device_id = device_id
        self.failure_mode = failure_mode
        self.current_version = "1.0"
        self.status = "quiescent"

    def on_connect(self):
        print(f"[device:{self.device_id}] Connected")
        self.start_handshake(self.device_id)
        # Device listens to replies from the System
        self.subscribe_uufi(direction="reply")

    def on_message(self, topic, device_id, sub_type, sub_folder, data):
        if device_id != self.device_id:
            return
        
        payload = data.get("payload", {})
        
        if sub_type == "config" and sub_folder == "update":
            self.handle_update(payload)

    def handle_update(self, payload):
        if self.status == "pending":
            return

        target_version = payload.get("version")
        url = payload.get("url")
        expected_sha256 = payload.get("sha256")
        
        print(f"[device:{self.device_id}] Receiving update config to {target_version}")
        self.status = "pending"
        self.report_state()
        
        threading.Thread(target=self.apply_update, args=(target_version, url, expected_sha256), daemon=True).start()

    def apply_update(self, version, url, expected_sha256):
        time.sleep(2)
        
        if self.failure_mode:
            self.status = "failure"
        else:
            try:
                success = True
                if url.startswith("file://"):
                    path = url[7:]
                    with open(path, 'rb') as f:
                        actual_sha256 = hashlib.sha256(f.read()).hexdigest()
                    if actual_sha256 != expected_sha256:
                        success = False
                
                if success:
                    self.current_version = version
                    self.status = "success"
                else:
                    self.status = "failure"
            except Exception:
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
        # Reflect state back through System
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

    device = ButlerDevice(args.device_id, failure_mode=args.failure)
    device.connect()
    
    threading.Thread(target=device.heartbeat, daemon=True).start()
    device.loop_forever()

if __name__ == "__main__":
    main()
