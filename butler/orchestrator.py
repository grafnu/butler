import time
import argparse
import sys
import threading
import os
from butler.common import ButlerBusFactory, get_default_conn_spec
from butler.blob_repo import BlobRepository

class ButlerOrchestrator:
    def __init__(self, conn_spec=None, failure_mode=False, timeout=60):
        conn_spec = conn_spec or get_default_conn_spec()
        self.bus = ButlerBusFactory(source="butler", conn_spec=conn_spec)
        # Proxy bus methods for compatibility or just use self.bus
        self.source = self.bus.source
        self.publish_uufi = self.bus.publish_uufi
        self.subscribe_uufi = self.bus.subscribe_uufi
        self.connect = self.bus.connect
        self.loop_start = self.bus.loop_start
        self.loop_forever = self.bus.loop_forever
        self.generate_nonce = self.bus.generate_nonce
        
        # Override bus message handler
        self.bus.on_connect = self.on_connect
        self.bus.on_message = self.on_message
        
        self.blob_repo = BlobRepository()
        self.failure_mode = failure_mode
        self.timeout = timeout
        self.settling_time = 5
        self.devices_pending = {} # device_id -> start_time
        self.known_devices = {} # device_id -> device_info
        self.last_action_time = {} # device_id -> time

    def on_connect(self):
        print("[butler] Orchestrator connected, starting handshake...")
        self.bus.start_handshake()
        # Subscribe to all traffic to handle device handshakes and states
        self.subscribe_uufi()

    def on_message(self, topic, device_id, sub_type, sub_folder, data):
        if not self.bus.handshake_complete:
            # Check for handshake reply in the common bus handler
            return

        # 1. Handle Cloud Model Replies (mocket -> System)
        if sub_type == "config" and sub_folder == "cloud":
            self.handle_cloud_reply(data)
            return

        # 2. Handle device state (Client -> System)
        if sub_type == "state" and sub_folder == "update":
            self.handle_device_state(device_id, data)

    def handle_cloud_reply(self, data):
        # Nested structure as per 3.2: {"devices": { "device_id": { "subsystem": { ... } } } }
        cloud_data = data.get("cloud", {})
        devices = cloud_data.get("devices")
        if devices:
            for device_id, subsystems in devices.items():
                if subsystems:
                    # For now, we just take the first subsystem data
                    subsystem_id, subsystem_data = next(iter(subsystems.items()))
                    print(f"[butler] Received model info for {device_id}/{subsystem_id}")
                    if device_id not in self.known_devices:
                        self.known_devices[device_id] = {}
                    self.known_devices[device_id].update(subsystem_data)
                    # Reconcile if needed
                    self.reconcile_device(device_id)
        elif cloud_data.get("status") == "success":
            print(f"[butler] Cloud model update confirmed.")

    def handle_device_state(self, device_id, data):
        # Look inside the 'update' subfolder for the device state
        update_state = data.get("update", {})
        current_version = update_state.get("version")
        lkg_version = update_state.get("lkg_version")
        status = update_state.get("status", "quiescent")
        registry_id = data.get("deviceRegistryId")
        
        print(f"[butler] Received device state for {device_id} (reg: {registry_id}): {status} (v{current_version}, lkg: {lkg_version})")
        
        device_info = self.known_devices.get(device_id)
        if not device_info:
            # If we don't know the device, query the model
            print(f"[butler] Unknown device {device_id}, querying model...")
            # We can store the registry_id here if we want to support multi-registry discovery
            self.known_devices[device_id] = {"registry_id": registry_id}
            self.query_model(device_id)
            return

        # Ensure registry_id is updated in cache
        if registry_id:
            device_info["registry_id"] = registry_id

        target_version = device_info.get("target_version")
        old_status = device_info.get("state")
        
        if status != old_status:
            self.last_action_time[device_id] = time.time()

        if status == "quiescent":
            if current_version != target_version:
                self.reconcile_device(device_id)
            else:
                if device_info.get("state") != "quiescent":
                    print(f"[butler] Device {device_id} is quiescent and compliant.")
                    self.update_model(device_id, current_version=current_version, last_known_good=lkg_version, state="quiescent")
        
        elif status == "success":
            if device_info.get("current_version") != current_version or device_info.get("state") != "quiescent":
                print(f"[butler] Device {device_id} success reported. Requesting model update.")
                self.update_model(device_id, current_version=current_version, last_known_good=lkg_version, state="quiescent")
                # Also update local cache
                device_info["current_version"] = current_version
                device_info["last_known_good"] = lkg_version
                device_info["state"] = "quiescent"
            if device_id in self.devices_pending:
                del self.devices_pending[device_id]
        
        elif status == "failure":
            print(f"[butler] Device {device_id} failure reported. Triggering rollback.")
            self.trigger_rollback(device_id, device_info)
            if device_id in self.devices_pending:
                del self.devices_pending[device_id]

    def query_model(self, device_id=None):
        payload = {
            "operation": "READ"
        }
        device_info = self.known_devices.get(device_id, {}) if device_id else {}
        registry_id = device_info.get("registry_id")
        self.publish_uufi(device_id, "query", payload, "cloud", registry_id=registry_id)

    def update_model(self, device_id, **detail):
        # Nested structure as per 3.2: {"devices": { "device_id": { "subsystem": { ... } } } }
        device_info = self.known_devices.get(device_id, {}).copy()
        device_info.update(detail)
        self.known_devices[device_id] = device_info # update cache
        
        subsystem = device_info.get("subsystem", "main")
        registry_id = device_info.get("registry_id")
        payload = {
            "operation": "UPDATE",
            "devices": {
                device_id: {
                    subsystem: device_info
                }
            }
        }
        self.publish_uufi(device_id, "model", payload, "cloud", registry_id=registry_id)
        self.last_action_time[device_id] = time.time()

    def trigger_update(self, device_id, device_info):
        if self.failure_mode:
            return

        version = device_info.get("target_version")
        make = device_info.get("make", "default")
        model = device_info.get("model", "default")
        subsystem = device_info.get("subsystem", "main")
        registry_id = device_info.get("registry_id")
        
        print(f"[butler] Triggering update for {device_id} to version {version}")
        metadata = self.blob_repo.get_blob_metadata(make, model, subsystem, version)
        if not metadata:
            print(f"[butler] No metadata found for {make}/{model}/{subsystem}/{version}")
            error_payload = {
                "category": "not_found",
                "message": f"No blob metadata found for {make}/{model}/{subsystem}/{version}",
                "transactionId": f"BUTLER:{self.generate_nonce()}"
            }
            self.publish_uufi(device_id, "errors", error_payload, "update", registry_id=registry_id)
            return
        
        payload = {
            "url": metadata["url"],
            "sha256": metadata["sha256"],
            "version": version
        }
        self.publish_uufi(device_id, "config", payload, "update", target_principal="mocket", registry_id=registry_id)
        self.devices_pending[device_id] = time.time()
        self.last_action_time[device_id] = time.time()
        # Update local cache and model
        device_info["state"] = "active"
        self.update_model(device_id, state="active")

    def trigger_rollback(self, device_id, device_info):
        lkg = device_info.get("last_known_good", "1.0")
        print(f"[butler] Rolling back {device_id} to {lkg}")
        # Update local cache and model
        device_info["target_version"] = lkg
        device_info["state"] = "error"
        self.update_model(device_id, target_version=lkg, state="error")
        self.last_action_time[device_id] = time.time()

    def reconcile_device(self, device_id):
        device_info = self.known_devices.get(device_id)
        if not device_info:
            return

        now = time.time()
        last_action = self.last_action_time.get(device_id, 0)
        if now - last_action < self.settling_time:
            return

        if device_info.get("current_version") != device_info.get("target_version"):
            if device_info.get("state") in ["quiescent", "error"]:
                self.trigger_update(device_id, device_info)

    def reconcile_all(self):
        # Instead of loading local file, query mocket
        print(f"[butler] Reconciling all devices, querying model...")
        self.query_model()

    def check_timeouts(self):
        while True:
            now = time.time()
            for device_id, start_time in list(self.devices_pending.items()):
                if now - start_time > self.timeout:
                    print(f"[butler] Timeout for {device_id} after {self.timeout}s")
                    device_info = self.known_devices.get(device_id)
                    if device_info:
                        self.trigger_rollback(device_id, device_info)
                    del self.devices_pending[device_id]
            time.sleep(1)

    def watch_model(self):
        # Since we don't watch the file, we can periodically query mocket
        # or rely on mocket pushing updates when it detects changes.
        # But the spec says butler should detect changes reported by mocket.
        # So mocket should probably publish its state periodically or on change.
        # For now, let's just query periodically.
        while True:
            self.query_model()
            time.sleep(10)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("conn_spec", help="Connection specification (e.g. mqtt://localhost:1883)")
    parser.add_argument("-f", "--failure", action="store_true", help="Enable failure mode")
    parser.add_argument("-t", "--timeout", type=int, default=60, help="Pending state timeout (seconds)")
    args = parser.parse_args()

    orchestrator = ButlerOrchestrator(conn_spec=args.conn_spec, failure_mode=args.failure, timeout=args.timeout)
    orchestrator.connect()
    # Start loop in a separate thread
    threading.Thread(target=orchestrator.loop_forever, daemon=True).start()
    
    print("[butler] Waiting for handshake completion...")
    if not orchestrator.bus.wait_for_handshake(timeout=20):
        print("[butler] Handshake timed out. Exiting.")
        sys.exit(1)

    threading.Thread(target=orchestrator.check_timeouts, daemon=True).start()
    threading.Thread(target=orchestrator.watch_model, daemon=True).start()
    
    # Initial reconciliation
    orchestrator.reconcile_all()
    
    # Block main thread
    while True:
        time.sleep(1)

if __name__ == "__main__":
    main()
