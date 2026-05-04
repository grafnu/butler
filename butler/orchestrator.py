import time
import argparse
import sys
import threading
import os
from butler.common import ButlerMQTTBase
from butler.blob_repo import BlobRepository
from butler.model_repo import ModelRepository

class ButlerOrchestrator(ButlerMQTTBase):
    def __init__(self, conn_spec=None, failure_mode=False, timeout=60):
        super().__init__(source="butler", conn_spec=conn_spec)
        self.blob_repo = BlobRepository()
        self.model_repo = ModelRepository()
        self.failure_mode = failure_mode
        self.timeout = timeout
        self.devices_pending = {} # device_id -> start_time
        self.known_devices = set()

    def on_connect(self):
        print("[butler] Orchestrator connected")
        # Orchestrator is the System, it doesn't need to handshake with itself
        self.handshake_complete = True
        # Subscribe to all traffic to handle device handshakes and states
        self.subscribe_uufi()

    def on_message(self, topic, device_id, sub_type, sub_folder, data):
        # 1. Handle UUFI Handshake (System side)
        if sub_type == "state" and sub_folder == "udmi":
            self.handle_handshake_state(device_id, data)
            return

        if not self.handshake_complete:
            return

        # 2. Handle device state (Client -> System)
        if sub_type == "state" and sub_folder == "update":
            self.handle_device_state(device_id, data)

    def handle_handshake_state(self, device_id, data):
        udmi = data.get("udmi", {})
        setup = udmi.get("setup", {})
        transaction_id = setup.get("transaction_id")
        source = data.get("source")
        
        if source == self.source:
            return

        print(f"[butler] Handling handshake from {source}")
        
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
        # Handshake addressing: /uufi/p/{principal}/{subType}/{subFolder}
        self.publish_uufi(device_id, "config", response_payload, "udmi", direction="reply", target_principal=source, transaction_id=transaction_id)

    def handle_device_state(self, device_id, data):
        current_version = data.get("version")
        status = data.get("status", "quiescent")
        
        print(f"[butler] Received device state for {device_id}: {status} (v{current_version})")
        
        device_info = self.model_repo.get_device(device_id)
        if not device_info:
            return

        self.known_devices.add(device_id)
        target_version = device_info.get("target_version")
        
        if status == "quiescent":
            if current_version != target_version:
                self.trigger_update(device_id, device_info)
            else:
                if device_info.get("state") != "quiescent":
                    print(f"[butler] Device {device_id} is quiescent and compliant.")
                    self.model_repo.update_device(device_id, current_version=current_version, state="quiescent")
        
        elif status == "success":
            if device_info.get("current_version") != current_version or device_info.get("state") != "quiescent":
                print(f"[butler] Device {device_id} success reported. Updating model.")
                self.model_repo.update_device(device_id, current_version=current_version, last_known_good=current_version, state="quiescent")
            if device_id in self.devices_pending:
                del self.devices_pending[device_id]
        
        elif status == "failure":
            print(f"[butler] Device {device_id} failure reported. Triggering rollback.")
            self.trigger_rollback(device_id, device_info)
            if device_id in self.devices_pending:
                del self.devices_pending[device_id]

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
        self.model_repo.update_device(device_id, state="active")

    def trigger_rollback(self, device_id, device_info):
        lkg = device_info.get("last_known_good", "1.0")
        print(f"[butler] Rolling back {device_id} to {lkg}")
        self.model_repo.update_device(device_id, target_version=lkg, state="error")

    def reconcile_all(self):
        model = self.model_repo.load_model()
        for device_id, device_info in model.items():
            self.known_devices.add(device_id)
            if device_info.get("current_version") != device_info.get("target_version"):
                if device_info.get("state") in ["quiescent", "error"]:
                    self.trigger_update(device_id, device_info)

    def check_timeouts(self):
        while True:
            now = time.time()
            for device_id, start_time in list(self.devices_pending.items()):
                if now - start_time > self.timeout:
                    print(f"[butler] Timeout for {device_id} after {self.timeout}s")
                    self.trigger_rollback(device_id, self.model_repo.get_device(device_id))
                    del self.devices_pending[device_id]
            time.sleep(1)

    def watch_model(self):
        last_mtime = 0
        while True:
            try:
                if os.path.exists(self.model_repo.model_file):
                    mtime = os.path.getmtime(self.model_repo.model_file)
                    if mtime > last_mtime:
                        # We wait a bit to let the write finish
                        time.sleep(0.5)
                        if last_mtime > 0:
                            print(f"[butler] Model file change detected.")
                            self.reconcile_all()
                        last_mtime = os.path.getmtime(self.model_repo.model_file)
            except OSError:
                pass
            time.sleep(1)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("conn_spec", nargs="?", help="Connection specification (e.g. mqtt://localhost:1883)")
    parser.add_argument("-f", "--failure", action="store_true", help="Enable failure mode")
    parser.add_argument("-t", "--timeout", type=int, default=60, help="Pending state timeout (seconds)")
    args = parser.parse_args()

    orchestrator = ButlerOrchestrator(conn_spec=args.conn_spec, failure_mode=args.failure, timeout=args.timeout)
    orchestrator.connect()
    
    threading.Thread(target=orchestrator.check_timeouts, daemon=True).start()
    threading.Thread(target=orchestrator.watch_model, daemon=True).start()
    
    # Initial reconciliation
    orchestrator.reconcile_all()
    
    orchestrator.loop_forever()

if __name__ == "__main__":
    main()
