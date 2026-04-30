import time
import argparse
import sys
import threading
from butler.common import ButlerMQTTBase
from butler.blob_repo import BlobRepository

class ButlerOrchestrator(ButlerMQTTBase):
    def __init__(self, failure_mode=False, timeout=60):
        super().__init__(source="butler")
        self.blob_repo = BlobRepository()
        self.failure_mode = failure_mode
        self.timeout = timeout
        self.devices_pending = {} # device_id -> start_time
        self.fleet_model = {} # device_id -> state

    def on_connect(self):
        print("[butler] Orchestrator connected")
        self.start_handshake()
        # Subscribe to all replies (System -> Client)
        self.subscribe_uufi(direction="reply")

    def on_message(self, topic, device_id, sub_type, sub_folder, data):
        if not self.handshake_complete:
            return

        # Handle model reply (System -> Client)
        if sub_type == "config" and sub_folder == "cloud":
            self.handle_model_update(device_id, data)
            return

        # Handle device state (reflected from System -> Client)
        if sub_type == "state" and sub_folder == "update":
            self.handle_device_state(device_id, data)

    def handle_model_update(self, device_id, data):
        if device_id == self.registry_id:
            return
        
        print(f"[butler] Received model update for {device_id}")
        self.fleet_model[device_id] = data
        self.reconcile_device(device_id)

    def handle_device_state(self, device_id, data):
        current_version = data.get("version")
        status = data.get("status", "quiescent")
        
        print(f"[butler] Received device state for {device_id}: {status} (v{current_version})")
        
        device_info = self.fleet_model.get(device_id)
        if not device_info:
            # Query the model if we don't have it
            self.query_model(device_id)
            return

        target_version = device_info.get("target_version")
        
        if status == "quiescent":
            if current_version != target_version:
                self.trigger_update(device_id, device_info)
            else:
                # Update model state to quiescent if it was something else
                if device_info.get("state") != "quiescent":
                    print(f"[butler] Device {device_id} is quiescent and compliant. Finalizing model state.")
                    self.update_cloud_model(device_id, current_version=current_version, state="quiescent")
        
        elif status == "success":
            print(f"[butler] Device {device_id} success reported. Updating cloud model.")
            self.update_cloud_model(device_id, current_version=current_version, last_known_good=current_version, state="quiescent")
            if device_id in self.devices_pending:
                del self.devices_pending[device_id]
        
        elif status == "failure":
            print(f"[butler] Device {device_id} failure reported. Triggering rollback.")
            self.trigger_rollback(device_id, device_info)
            if device_id in self.devices_pending:
                del self.devices_pending[device_id]

    def query_model(self, device_id):
        print(f"[butler] Querying cloud model for {device_id}")
        payload = {"operation": "READ"}
        self.publish_uufi(device_id, "query", payload, "cloud")

    def update_cloud_model(self, device_id, **kwargs):
        print(f"[butler] Updating cloud model for {device_id}: {kwargs}")
        payload = {"operation": "UPDATE"}
        payload.update(kwargs)
        self.publish_uufi(device_id, "model", payload, "cloud")
        # Optimistically update local model
        if device_id in self.fleet_model:
            self.fleet_model[device_id].update(kwargs)

    def trigger_update(self, device_id, device_info):
        if self.failure_mode:
            return

        version = device_info.get("target_version")
        make = device_info.get("make", "default")
        model = device_info.get("model", "default")
        subsystem = device_info.get("subsystem", "default")
        
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
        self.update_cloud_model(device_id, state="active")

    def trigger_rollback(self, device_id, device_info):
        lkg = device_info.get("last_known_good", "1.0")
        print(f"[butler] Rolling back {device_id} to {lkg}")
        self.update_cloud_model(device_id, target_version=lkg, state="error")

    def reconcile_device(self, device_id):
        device_info = self.fleet_model.get(device_id)
        if device_info and device_info.get("current_version") != device_info.get("target_version"):
            if device_info.get("state") == "quiescent":
                self.trigger_update(device_id, device_info)

    def check_timeouts(self):
        while True:
            now = time.time()
            for device_id, start_time in list(self.devices_pending.items()):
                if now - start_time > self.timeout:
                    print(f"[butler] Timeout for {device_id} after {self.timeout}s")
                    self.trigger_rollback(device_id, self.fleet_model.get(device_id, {}))
                    del self.devices_pending[device_id]
            time.sleep(1)

    def poll_model(self):
        while True:
            if self.handshake_complete:
                for device_id in list(self.fleet_model.keys()):
                    self.query_model(device_id)
            time.sleep(15)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("-f", "--failure", action="store_true", help="Enable failure mode")
    parser.add_argument("-t", "--timeout", type=int, default=60, help="Pending state timeout (seconds)")
    args = parser.parse_args()

    orchestrator = ButlerOrchestrator(failure_mode=args.failure, timeout=args.timeout)
    orchestrator.connect()
    
    threading.Thread(target=orchestrator.check_timeouts, daemon=True).start()
    threading.Thread(target=orchestrator.poll_model, daemon=True).start()
    
    orchestrator.loop_forever()

if __name__ == "__main__":
    main()
