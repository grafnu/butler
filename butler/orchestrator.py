import time
import argparse
import sys
import threading
from butler.common import ButlerMQTTBase
from butler.blob_repo import BlobRepository
from butler.model_repo import ModelRepository

class ButlerOrchestrator(ButlerMQTTBase):
    def __init__(self, failure_mode=False):
        super().__init__(source="butler")
        self.model_repo = ModelRepository()
        self.blob_repo = BlobRepository()
        self.failure_mode = failure_mode
        self.devices_pending = {} # device_id -> start_time

    def on_connect(self):
        print("[butler] Orchestrator connected")
        self.start_handshake()
        # Subscribe to device state updates (Reflected back from System)
        self.subscribe_uufi(direction="reply")

    def on_message(self, topic, device_id, sub_type, sub_folder, data):
        if not self.handshake_complete:
            return

        payload = data.get("payload", {})
        
        # We listen for device state updates
        if sub_type == "state" and sub_folder == "update":
            self.handle_device_state(device_id, payload)

    def handle_device_state(self, device_id, payload):
        current_version = payload.get("version")
        status = payload.get("status", "quiescent")
        
        device_model = self.model_repo.get_device(device_id)
        target_version = device_model.get("target_version")
        
        print(f"[butler] Device {device_id} state: current={current_version}, status={status}, target={target_version}")
        
        if status == "quiescent":
            if current_version != target_version:
                self.trigger_update(device_id, device_model)
            else:
                self.model_repo.update_device(device_id, current_version=current_version, state="quiescent")
        
        elif status == "success":
            print(f"[butler] Device {device_id} success. Updating model.")
            self.model_repo.update_device(device_id, current_version=current_version, last_known_good=current_version, state="quiescent")
            if device_id in self.devices_pending:
                del self.devices_pending[device_id]
        
        elif status == "failure":
            print(f"[butler] Device {device_id} failure. Triggering rollback.")
            self.trigger_rollback(device_id, device_model)
            if device_id in self.devices_pending:
                del self.devices_pending[device_id]

    def trigger_update(self, device_id, device_model):
        if self.failure_mode:
            return

        version = device_model.get("target_version")
        make = device_model.get("make", "default")
        model = device_model.get("model", "default")
        subsystem = device_model.get("subsystem", "default")
        
        print(f"[butler] Triggering update for {device_id} to version {version}")
        metadata = self.blob_repo.get_blob_metadata(make, model, subsystem, version)
        if not metadata:
            print(f"[butler] No metadata found for {make}/{model}/{subsystem}/{version}")
            return
        
        payload = {
            "url": metadata["url"],
            "sha256": metadata["sha256"],
            "version": version
        }
        self.publish_uufi(device_id, "config", payload, "update")
        self.devices_pending[device_id] = time.time()
        self.model_repo.update_device(device_id, state="active")

    def trigger_rollback(self, device_id, device_model):
        lkg = device_model.get("last_known_good", "1.0")
        print(f"[butler] Rolling back {device_id} to {lkg}")
        self.model_repo.update_device(device_id, target_version=lkg, state="error")

    def check_timeouts(self):
        while True:
            now = time.time()
            for device_id, start_time in list(self.devices_pending.items()):
                if now - start_time > 60:
                    print(f"[butler] Timeout for {device_id}")
                    self.trigger_rollback(device_id, self.model_repo.get_device(device_id))
                    del self.devices_pending[device_id]
            time.sleep(1)

    def reconcile(self):
        while True:
            if self.handshake_complete:
                model = self.model_repo.load_model()
                for device_id, device in model.items():
                    if device.get("current_version") != device.get("target_version") and device.get("state") == "quiescent":
                        self.trigger_update(device_id, device)
            time.sleep(5)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("-f", "--failure", action="store_true", help="Enable failure mode")
    args = parser.parse_args()

    orchestrator = ButlerOrchestrator(failure_mode=args.failure)
    orchestrator.connect()
    
    threading.Thread(target=orchestrator.check_timeouts, daemon=True).start()
    threading.Thread(target=orchestrator.reconcile, daemon=True).start()
    
    orchestrator.loop_forever()

if __name__ == "__main__":
    main()
