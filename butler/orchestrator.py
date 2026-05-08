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
        self.devices_pending = {} # "registry_id/device_id/subsystem" -> start_time
        self.known_devices = {} # "registry_id/device_id/subsystem" -> device_info
        self.last_action_time = {} # "registry_id/device_id/subsystem" -> time

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
        # Nested structure as per 3.2: {"registries": { "registry_id": { "devices": { "device_id": { "subsystem": { ... } } } } } }
        cloud_data = data.get("cloud", {})
        registries = cloud_data.get("registries")
        if registries:
            for registry_id, reg_data in registries.items():
                devices = reg_data.get("devices", {})
                for device_id, subsystems in devices.items():
                    for subsystem_id, subsystem_data in subsystems.items():
                        sub_key = f"{registry_id}/{device_id}/{subsystem_id}"
                        
                        print(f"[butler] Received model info for {sub_key}: {subsystem_data}")
                        if sub_key not in self.known_devices:
                            self.known_devices[sub_key] = {"registry_id": registry_id, "device_id": device_id, "subsystem": subsystem_id}
                        
                        self.known_devices[sub_key].update(subsystem_data)
                        # Reconcile if needed
                        self.reconcile_device(registry_id, device_id, subsystem_id)
        elif cloud_data.get("status") == "success":
            print(f"[butler] Cloud model update confirmed.")

    def handle_device_state(self, device_id, data):
        # Look inside the 'update' subfolder for the device state
        update_state = data.get("update", {})
        current_version = update_state.get("current_version") or ""
        lkg_version = update_state.get("lkg_version")
        status = update_state.get("status", "quiescent")
        registry_id = data.get("deviceRegistryId")
        
        if not registry_id:
            return

        subsystem = "main" # Default subsystem for now
        sub_key = f"{registry_id}/{device_id}/{subsystem}"
        print(f"[butler] Received device state for {sub_key}: {status} (v{current_version}, lkg: {lkg_version})")
        
        device_info = self.known_devices.get(sub_key)
        if not device_info:
            # If we don't know the device, query the model
            print(f"[butler] Unknown device {sub_key}, querying model...")
            self.known_devices[sub_key] = {"registry_id": registry_id, "device_id": device_id, "subsystem": subsystem}
            self.query_model(registry_id, device_id)
            return

        updates = {}

        # Always trust and persist LKG if reported
        if lkg_version and lkg_version != device_info.get("lkg_version"):
            print(f"[butler] Updating LKG for {sub_key} to {lkg_version}")
            updates["lkg_version"] = lkg_version

        old_status = device_info.get("state")
        if status != old_status:
            self.last_action_time[sub_key] = time.time()

        if status == "pending":
            if sub_key not in self.devices_pending:
                print(f"[butler] Device {sub_key} entered pending state. Starting timeout timer.")
                self.devices_pending[sub_key] = time.time()
        
        elif status == "quiescent":
            target_version = device_info.get("target_version")
            if current_version != target_version:
                self.reconcile_device(registry_id, device_id, subsystem)
            else:
                if device_info.get("state") != "quiescent" or device_info.get("current_version") != current_version:
                    print(f"[butler] Device {sub_key} is quiescent and compliant.")
                    updates["current_version"] = current_version
                    updates["state"] = "quiescent"
        
        elif status == "success":
            if device_info.get("current_version") != current_version or device_info.get("state") != "quiescent":
                print(f"[butler] Device {sub_key} success reported. Requesting model update.")
                updates["current_version"] = current_version
                updates["lkg_version"] = lkg_version
                updates["state"] = "quiescent"
            if sub_key in self.devices_pending:
                del self.devices_pending[sub_key]
        
        elif status == "failure":
            print(f"[butler] Device {sub_key} failure reported. Triggering rollback.")
            self.trigger_rollback(registry_id, device_id, device_info)
            if sub_key in self.devices_pending:
                del self.devices_pending[sub_key]

        if updates:
            self.update_model(registry_id, device_id, subsystem=subsystem, **updates)

    def query_model(self, registry_id=None, device_id=None):
        payload = {
            "operation": "READ"
        }
        self.publish_uufi(device_id, "query", payload, "cloud", registry_id=registry_id)

    def update_model(self, registry_id, device_id, subsystem="main", **detail):
        sub_key = f"{registry_id}/{device_id}/{subsystem}"
        
        # Ingest and cache state updates ALWAYS (as per spec)
        device_info = self.known_devices.get(sub_key, {}).copy()
        device_info.update(detail)
        self.known_devices[sub_key] = device_info
        
        # Only send the changed fields to avoid overwriting with stale cache
        payload = {
            "operation": "UPDATE",
            "registries": {
                registry_id: {
                    "devices": {
                        device_id: {
                            subsystem: detail
                        }
                    }
                }
            }
        }
        self.publish_uufi(device_id, "model", payload, "cloud", registry_id=registry_id)

    def trigger_update(self, registry_id, device_id, device_info):
        if self.failure_mode:
            return

        subsystem = device_info.get("subsystem", "main")
        sub_key = f"{registry_id}/{device_id}/{subsystem}"
        
        version = device_info.get("target_version")
        make = device_info.get("make", "default")
        model = device_info.get("model", "default")
        
        print(f"[butler] Triggering update for {sub_key} to version {version}")
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
        self.publish_uufi(device_id, "config", payload, "update", registry_id=registry_id)
        self.last_action_time[sub_key] = time.time()
        # Update local cache and model
        self.update_model(registry_id, device_id, subsystem=subsystem, state="active", active_version=version)

    def trigger_rollback(self, registry_id, device_id, device_info):
        subsystem = device_info.get("subsystem", "main")
        sub_key = f"{registry_id}/{device_id}/{subsystem}"

        lkg = device_info.get("lkg_version", "1.0")
        print(f"[butler] Rolling back {sub_key} to {lkg}")
        # Update local cache and model
        self.last_action_time[sub_key] = time.time()
        self.update_model(registry_id, device_id, subsystem=subsystem, target_version=lkg, state="error")

    def reconcile_device(self, registry_id, device_id, subsystem_id="main"):
        sub_key = f"{registry_id}/{device_id}/{subsystem_id}"
        device_info = self.known_devices.get(sub_key)
        if not device_info:
            return

        # Settling time check: prevent re-evaluation within 5s of last action/state change
        now = time.time()
        last_action = self.last_action_time.get(sub_key, 0)
        if now - last_action < self.settling_time:
            return

        current = device_info.get("current_version")
        target = device_info.get("target_version")
        state = device_info.get("state")
        
        if current != target:
            print(f"[butler] Reconciling {sub_key}: current={current}, target={target}, state={state}")
            # Re-triggering Logic: allow if quiescent/error OR if pending but target changed
            if state in ["quiescent", "error", None]:
                self.trigger_update(registry_id, device_id, device_info)
            elif state == "active" or sub_key in self.devices_pending:
                # ONLY if target_version has changed to a value different from the version currently being applied
                active_version = device_info.get("active_version")
                if target != active_version:
                    print(f"[butler] Target version changed for {sub_key} while pending. Re-triggering.")
                    self.trigger_update(registry_id, device_id, device_info)

    def reconcile_all(self):
        # Instead of loading local file, query mocket
        print(f"[butler] Reconciling all devices, querying model...")
        self.query_model()

    def check_timeouts(self):
        while True:
            now = time.time()
            for sub_key, start_time in list(self.devices_pending.items()):
                if now - start_time > self.timeout:
                    parts = sub_key.split("/")
                    if len(parts) == 3:
                        registry_id, device_id, subsystem = parts
                    else:
                        registry_id, device_id = parts
                        subsystem = "main"
                    print(f"[butler] Timeout for {sub_key} after {self.timeout}s")
                    device_info = self.known_devices.get(sub_key)
                    if device_info:
                        self.trigger_rollback(registry_id, device_id, device_info)
                    del self.devices_pending[sub_key]
            time.sleep(1)

    def watch_model(self):
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
    if not orchestrator.bus.wait_for_handshake(timeout=60):
        print("[CRITICAL] Handshake timed out after 60s. Exiting.", file=sys.stderr)
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
