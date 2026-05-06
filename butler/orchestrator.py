import json
import time
import sys
import os
import argparse
from butler.blob_repo import BlobRepository
from butler.messaging import create_payload, create_envelope
from butler.conn_spec import parse_conn_spec
from butler.transport import get_transport

class Orchestrator:
    def __init__(self, conn_spec, fail_mode=False):
        self.conn_spec = conn_spec
        self.blob_repo = BlobRepository()
        self.fail_mode = fail_mode
        self.transport = get_transport(conn_spec)
        self.pending_updates = {} # device_id: {timestamp, target_version, subsystem}
        self.seen_nonces = set()
        self.is_active = False
        self.handshake_tid = None
        self.registry_id = "butler-registry"
        self.model = {}
        self.principal = self.conn_spec.principal or "butler"

    def on_message(self, env, payload, topic, raw=None):
        if not payload: return
        sub_type = env.get("subType")
        sub_folder = env.get("subFolder")
        device_id = env.get("deviceId")
        
        # Handshake reply
        if sub_type == "config" and sub_folder == "udmi" and not device_id:
            self.handle_handshake_reply(payload, env.get("transactionId"))
            return

        # Cloud model update
        if sub_folder == "cloud" and device_id == self.registry_id:
            self.model = payload.get("cloud", payload)
            print(f"[butler] Received model update from cloud", flush=True)
            return

        # Device state update
        if sub_type == "state" and sub_folder == "update":
            nonce = env.get("nonce")
            if nonce in self.seen_nonces: return
            self.seen_nonces.add(nonce)
            if len(self.seen_nonces) > 1000: self.seen_nonces.clear()

            update = payload.get("update", {})
            subsystem = update.get("subsystem", "main")
            state = update.get("state")
            current_version = update.get("current_version")

            print(f"[butler] Status from {device_id}: {state} ({current_version})", flush=True)

            if state == "success" or state == "quiescent":
                self.update_cloud_model(device_id, subsystem, current_version=current_version)
                if state == "success" and device_id in self.pending_updates:
                    del self.pending_updates[device_id]
            elif state == "failure":
                print(f"[butler] Device {device_id} reported FAILURE. Rolling back...", flush=True)
                self.rollback_cloud_model(device_id, subsystem)
                if device_id in self.pending_updates:
                    del self.pending_updates[device_id]

    def handle_handshake_reply(self, payload, tid):
        udmi = payload.get("udmi", payload)
        reply = udmi.get("reply", {})
        reply_tid = reply.get("transaction_id")
        if reply_tid == self.handshake_tid:
            print(f"[butler] UUFI Handshake complete (tid: {reply_tid}). Orchestrator is ACTIVE.", flush=True)
            self.is_active = True
            self.query_cloud_model()

    def query_cloud_model(self):
        env = create_envelope(
            registry_id=self.registry_id,
            device_id=self.registry_id,
            sub_type="query",
            sub_folder="cloud",
            source="butler"
        )
        payload = create_payload("cloud", {"operation": "READ"})
        self.transport.publish(env, payload)

    def update_cloud_model(self, device_id, subsystem, target_version=None, current_version=None):
        subsystem_data = {}
        if target_version: subsystem_data["target_version"] = target_version
        if current_version: subsystem_data["current_version"] = current_version
        
        payload_data = {
            "operation": "UPDATE",
            "devices": {
                device_id: {
                    subsystem: subsystem_data
                }
            }
        }
        
        env = create_envelope(
            registry_id=self.registry_id,
            device_id=self.registry_id,
            sub_type="model",
            sub_folder="cloud",
            source="butler"
        )
        payload = create_payload("cloud", payload_data)
        self.transport.publish(env, payload)

    def rollback_cloud_model(self, device_id, subsystem):
        devices = self.model.get("devices", self.model)
        dev_info = devices.get(device_id, {}).get(subsystem, {})
        lkg = dev_info.get("lkg_version", "0.0.0")
        self.update_cloud_model(device_id, subsystem, target_version=lkg)

    def check_reconciliation(self):
        if not self.is_active or not isinstance(self.model, dict):
            return
        
        devices = self.model.get("devices", self.model)
        if not isinstance(devices, dict):
            return

        for device_id, subsystems in devices.items():
            if not isinstance(subsystems, dict): continue
            for subsystem, info in subsystems.items():
                if not isinstance(info, dict): continue
                target = info.get("target_version")
                current = info.get("current_version")
                
                if target != current and device_id not in self.pending_updates:
                    print(f"[butler] Reconciliation triggered for {device_id}: {current} -> {target}", flush=True)
                    
                    if self.fail_mode: continue

                    metadata = self.blob_repo.get_blob_metadata(
                        info.get("make"), info.get("model"), subsystem, target
                    )
                    
                    if metadata:
                        update_data = {
                            "version": target,
                            "url": metadata["url"],
                            "sha256": metadata["sha256"]
                        }
                        env = create_envelope(
                            registry_id=self.registry_id,
                            device_id=device_id,
                            sub_type="config",
                            sub_folder="update",
                            source="butler"
                        )
                        payload = create_payload("update", update_data)
                        self.transport.publish(env, payload)
                        self.pending_updates[device_id] = {
                            "timestamp": time.time(),
                            "target_version": target,
                            "subsystem": subsystem
                        }

    def check_timeouts(self):
        now = time.time()
        timeout = int(os.environ.get("BUTLER_TIMEOUT", 60))
        to_remove = []
        for device_id, info in self.pending_updates.items():
            if now - info["timestamp"] > timeout:
                print(f"[butler] Timeout for {device_id}. Rolling back...", flush=True)
                self.rollback_cloud_model(device_id, info["subsystem"])
                to_remove.append(device_id)
        
        for d in to_remove:
            del self.pending_updates[d]

    def send_handshake(self):
        self.handshake_tid = f"handshake-{int(time.time())}"
        udmi_payload = {
            "setup": {
                "functions_ver": 9,
                "transaction_id": self.handshake_tid,
                "msg_source": self.principal,
                "user": self.principal
            }
        }
        env = create_envelope(
            registry_id="",
            device_id="",
            sub_type="state",
            sub_folder="udmi",
            transaction_id=self.handshake_tid,
            source=self.principal
        )
        env["principal"] = self.principal
            
        payload = create_payload("udmi", udmi_payload)
        self.transport.publish(env, payload)

    def run(self):
        self.transport.connect()
        
        # Subscribe to handshake reply
        if self.conn_spec.protocol == "mqtt":
            self.transport.subscribe(f"/uufi/{self.conn_spec.prefix + '/' if self.conn_spec.prefix else ''}p/{self.principal}/config/udmi", self.on_message)
            self.transport.subscribe(f"/uufi/{self.conn_spec.prefix + '/' if self.conn_spec.prefix else ''}r/+/d/+/state/update", self.on_message)
            self.transport.subscribe(f"/uufi/{self.conn_spec.prefix + '/' if self.conn_spec.prefix else ''}r/{self.registry_id}/d/{self.registry_id}/config/cloud", self.on_message)
        else:
            self.transport.subscribe(self.on_message)

        self.transport.loop_start()
        
        last_handshake = 0
        try:
            while True:
                if not self.is_active:
                    now = time.time()
                    if now - last_handshake > 5:
                        self.send_handshake()
                        last_handshake = now
                
                if self.is_active:
                    self.check_reconciliation()
                    self.check_timeouts()
                time.sleep(2)
        except KeyboardInterrupt:
            self.transport.loop_stop()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("conn_spec", nargs="?", help="Connection spec URL")
    parser.add_argument("-f", action="store_true", help="Enable failure mode")
    args = parser.parse_args()

    conn_spec = parse_conn_spec(args.conn_spec, differentiator="butler")
    orchestrator = Orchestrator(conn_spec, fail_mode=args.f)
    print(f"Starting Butler Orchestrator with {conn_spec}...")
    orchestrator.run()

if __name__ == "__main__":
    main()
