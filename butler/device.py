import time
import argparse
import threading
import hashlib
import os
import json
from butler.common import ButlerMQTTBase
from butler.model_repo import ModelRepository

class MocketSystem(ButlerMQTTBase):
    def __init__(self, device_id, failure_mode=False):
        # Mocket acts as both the UDMIS System and the target device
        super().__init__(source="mockit")
        self.target_device_id = device_id
        self.failure_mode = failure_mode
        self.model_repo = ModelRepository()
        self.current_version = "1.0"
        self.status = "quiescent"
        self.clients = set()

    def on_connect(self):
        print(f"[mockit] System/Device connected for {self.target_device_id}")
        self.subscribe_uufi(direction="reflect")

    def on_message(self, topic, device_id, sub_type, sub_folder, data):
        source = data.get("source")
        
        # 1. Handle UUFI Handshake (System side)
        if sub_type == "state" and sub_folder == "udmi":
            self.handle_handshake_state(device_id, source, data)
            return

        # 2. Handle Cloud Model Queries/Updates
        if sub_type == "query" and sub_folder == "cloud":
            self.handle_cloud_query(device_id, source, data)
            return
        if sub_type == "model" and sub_folder == "cloud":
            self.handle_cloud_model(device_id, source, data)
            return

        # 3. Handle Device Config (Target Device side)
        if device_id == self.target_device_id and sub_type == "config" and sub_folder == "update":
            self.handle_update_config(source, data)

        # 4. Relay reflect to reply (Except handshakes which are handled above)
        # We relay everything else to ensure visibility and protocol compliance
        # In UUFI, state reflects are often relayed to all interested clients
        if direction_from_topic(topic) == "reflect":
            self.publish_uufi(device_id, sub_type, data, sub_folder, direction="reply")

    def handle_handshake_state(self, device_id, source, data):
        udmi = data.get("udmi", {})
        setup = udmi.get("setup", {})
        transaction_id = setup.get("transaction_id")
        
        print(f"[mockit] Handling handshake from {source}")
        if source:
            self.clients.add(source)
        
        response_payload = {
            "udmi": {
                "setup": {
                    "functions_min": 9,
                    "functions_max": 9,
                    "udmi_version": "1.5.2"
                },
                "reply": {
                    "functions_ver": 9,
                    "transaction_id": transaction_id,
                    "msg_source": source
                }
            }
        }
        self.publish_uufi(device_id, "config", response_payload, "udmi", direction="reply", target_source=source, transaction_id=transaction_id)
        
        if source:
            threading.Thread(target=self.notify_client_of_model, args=(source,), daemon=True).start()

    def handle_cloud_query(self, device_id, source, data):
        operation = data.get("operation")
        if operation == "READ":
            print(f"[mockit] Handling cloud model query from {source} for {device_id}")
            device_state = self.model_repo.get_device(device_id)
            self.publish_uufi(device_id, "config", device_state, "cloud", direction="reply", target_source=source)

    def handle_cloud_model(self, device_id, source, data):
        operation = data.get("operation")
        print(f"[mockit] Handling cloud model update from {source} for {device_id}: {operation}")
        state_update = {k: v for k, v in data.items() if k not in ["operation", "uufi_version", "publish_time", "source", "nonce", "transaction_id"]}
        self.model_repo.update_device(device_id, **state_update)
        self.notify_clients_of_model_change()

    def handle_update_config(self, source, data):
        if self.status == "pending":
            return
        target_version = data.get("version")
        url = data.get("url")
        expected_sha256 = data.get("sha256")
        
        print(f"[mockit] Device {self.target_device_id} receiving update config to {target_version}")
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
        self.publish_uufi(self.target_device_id, "state", payload, "update", direction="reflect")

    def notify_client_of_model(self, client):
        model = self.model_repo.load_model()
        for device_id, device_state in model.items():
            self.publish_uufi(device_id, "config", device_state, "cloud", direction="reply", target_source=client)

    def notify_clients_of_model_change(self):
        model = self.model_repo.load_model()
        for client in self.clients:
            for device_id, device_state in model.items():
                self.publish_uufi(device_id, "config", device_state, "cloud", direction="reply", target_source=client)

    def watch_model_file(self):
        last_mtime = 0
        while True:
            try:
                if os.path.exists(self.model_repo.model_file):
                    mtime = os.path.getmtime(self.model_repo.model_file)
                    if mtime > last_mtime:
                        if last_mtime > 0:
                            print(f"[mockit] Model file changed, notifying clients")
                            self.notify_clients_of_model_change()
                        last_mtime = mtime
            except OSError:
                pass
            time.sleep(1)

    def heartbeat(self):
        while True:
            self.report_state()
            time.sleep(30)

def direction_from_topic(topic):
    parts = topic.split('/')
    if len(parts) > 1:
        return parts[1]
    return None

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("device_id")
    parser.add_argument("-f", "--failure", action="store_true", help="Enable failure mode")
    args = parser.parse_args()
    mocket = MocketSystem(args.device_id, failure_mode=args.failure)
    mocket.connect()
    threading.Thread(target=mocket.heartbeat, daemon=True).start()
    threading.Thread(target=mocket.watch_model_file, daemon=True).start()
    mocket.loop_forever()

if __name__ == "__main__":
    main()
