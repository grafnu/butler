import json
import time
import sys
import os
import argparse
import datetime
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
        self.pending_updates = {} # (registry_id, device_id, subsystem): {timestamp, target_version}
        self.settle_times = {} # (registry_id, device_id, subsystem): timestamp
        self.nonce_history = {} # nonce: timestamp
        self.is_active = False
        self.handshake_tid = None
        self.handshake_start_time = None
        self.models = {} # registry_id: model_data (the "devices" dict)
        self.principal = self.conn_spec.principal or "butler"

    def on_message(self, env, payload, topic, raw=None):
        if not payload: return
        
        # Nonce tracking (5 minutes)
        nonce = env.get("nonce")
        if nonce:
            now = time.time()
            if nonce in self.nonce_history:
                return
            self.nonce_history[nonce] = now
            # Cleanup old nonces
            to_del = [n for n, t in self.nonce_history.items() if now - t > 300]
            for n in to_del: del self.nonce_history[n]

        sub_type = env.get("subType")
        sub_folder = env.get("subFolder")
        device_id = env.get("deviceId")
        registry_id = env.get("deviceRegistryId")
        principal = env.get("principal")

        # Filtering by principal for Handshake replies
        if sub_type == "config" and sub_folder == "udmi" and not device_id:
            if principal and principal != self.principal:
                return
            self.handle_handshake_reply(payload, env.get("transactionId"))
            return

        # Cloud model update
        if sub_folder == "cloud" and sub_type == "config":
            cloud = payload.get("cloud", payload)
            registries = cloud.get("registries", {})
            for reg_id, reg_data in registries.items():
                if reg_id not in self.models:
                    self.models[reg_id] = {}
                devices = reg_data.get("devices", {})
                for dev_id, dev_data in devices.items():
                    if dev_id not in self.models[reg_id]:
                        self.models[reg_id][dev_id] = {}
                    for sub, sub_data in dev_data.items():
                        if sub not in self.models[reg_id][dev_id]:
                            self.models[reg_id][dev_id][sub] = {}
                        self.models[reg_id][dev_id][sub].update(sub_data)
                print(f"[butler] Received model update for registry {reg_id}", flush=True)
            return

        # Device state update
        if sub_type == "state" and sub_folder == "update":
            update = payload.get("update", {})
            subsystem = update.get("subsystem", "main")
            status = update.get("status")
            current_version = update.get("current_version")
            lkg_version = update.get("lkg_version")

            if not registry_id or not device_id: return

            print(f"[butler] Status from {registry_id}/{device_id}: {status} ({current_version}, lkg: {lkg_version})", flush=True)
            
            key = (registry_id, device_id, subsystem)
            self.settle_times[key] = time.time()

            # Always update LKG if reported, and update current_version
            self.update_cloud_model(registry_id, device_id, subsystem, current_version=current_version, lkg_version=lkg_version, status=status)
            
            if status in ["success", "failure"]:
                if key in self.pending_updates:
                    del self.pending_updates[key]
                if status == "failure":
                    print(f"[butler] Device {registry_id}/{device_id} reported FAILURE. Rolling back...", flush=True)
                    self.rollback_cloud_model(registry_id, device_id, subsystem)

    def handle_handshake_reply(self, payload, tid):
        udmi = payload.get("udmi", payload)
        reply = udmi.get("reply", {})
        reply_tid = reply.get("transaction_id")
        if reply_tid == self.handshake_tid:
            print(f"[butler] UUFI Handshake complete (tid: {reply_tid}). Orchestrator is ACTIVE.", flush=True)
            self.is_active = True
            # Proactively query for registries
            self.query_all_registries()

    def query_all_registries(self):
        # Query using the dedicated discovery topic /uufi/c/query/cloud
        env = create_envelope(
            sub_type="query",
            sub_folder="cloud",
            source=self.principal,
            principal=self.principal
        )
        payload = create_payload("cloud", {"operation": "READ"})
        self.transport.publish(env, payload)

    def update_cloud_model(self, registry_id, device_id, subsystem, target_version=None, current_version=None, lkg_version=None, status=None):
        subsystem_data = {}
        if target_version: subsystem_data["target_version"] = target_version
        if current_version is not None: subsystem_data["current_version"] = current_version
        if lkg_version: subsystem_data["lkg_version"] = lkg_version
        if status is not None: subsystem_data["status"] = status
        
        payload_data = {
            "operation": "UPDATE",
            "registries": {
                registry_id: {
                    "devices": {
                        device_id: {
                            subsystem: subsystem_data
                        }
                    }
                }
            }
        }
        
        env = create_envelope(
            registry_id=registry_id,
            device_id=registry_id, # Target mocket handling the registry
            sub_type="model",
            sub_folder="cloud",
            source=self.principal
        )
        payload = create_payload("cloud", payload_data)
        self.transport.publish(env, payload)

    def rollback_cloud_model(self, registry_id, device_id, subsystem):
        devices = self.models.get(registry_id, {})
        dev_info = devices.get(device_id, {}).get(subsystem, {})
        lkg = dev_info.get("lkg_version", "0.0.0")
        status = dev_info.get("status")
        self.update_cloud_model(registry_id, device_id, subsystem, target_version=lkg, status=status)

    def check_reconciliation(self):
        if not self.is_active:
            return
        
        now = time.time()
        for registry_id, devices in self.models.items():
            for device_id, subsystems in devices.items():
                if not isinstance(subsystems, dict): continue
                for subsystem, info in subsystems.items():
                    if not isinstance(info, dict): continue
                    
                    key = (registry_id, device_id, subsystem)
                    
                    # Settling time check (5s)
                    last_action = self.settle_times.get(key, 0)
                    if now - last_action < 5:
                        continue

                    target = info.get("target_version")
                    current = info.get("current_version") or ""
                    
                    retrigger = False
                    if key in self.pending_updates:
                        pending_target = self.pending_updates[key]["target_version"]
                        if target != pending_target and target != current:
                            print(f"[butler] Retriggering {key}: target {target} != pending {pending_target}", flush=True)
                            retrigger = True
                    elif target and target != current:
                        retrigger = True

                    if retrigger:
                        print(f"[butler] Reconciliation triggered for {registry_id}/{device_id}: {current} -> {target}", flush=True)
                        
                        if self.fail_mode: continue

                        metadata = self.blob_repo.get_blob_metadata(
                            info.get("make"), info.get("model"), subsystem, target
                        )
                        
                        if metadata:
                            update_data = {
                                "version": target,
                                "url": metadata["url"],
                                "sha256": metadata["sha256"],
                                "subsystem": subsystem
                            }
                            env = create_envelope(
                                registry_id=registry_id,
                                device_id=device_id,
                                sub_type="config",
                                sub_folder="update",
                                source=self.principal
                            )
                            payload = create_payload("update", update_data)
                            self.transport.publish(env, payload)
                            self.pending_updates[key] = {
                                "timestamp": time.time(),
                                "target_version": target
                            }
                            self.settle_times[key] = time.time()

    def check_timeouts(self):
        now = time.time()
        timeout = int(os.environ.get("BUTLER_TIMEOUT", 60))
        to_remove = []
        for key, info in self.pending_updates.items():
            if now - info["timestamp"] > timeout:
                registry_id, device_id, subsystem = key
                print(f"[butler] Timeout for {registry_id}/{device_id}. Rolling back...", flush=True)
                self.rollback_cloud_model(registry_id, device_id, subsystem)
                to_remove.append(key)
        
        for k in to_remove:
            del self.pending_updates[k]

    def send_handshake(self):
        self.handshake_tid = f"handshake-{int(time.time())}"
        if self.handshake_start_time is None:
            self.handshake_start_time = time.time()
        udmi_payload = {
            "setup": {
                "functions_ver": 9,
                "transaction_id": self.handshake_tid,
                "msg_source": self.principal,
                "user": self.principal
            }
        }
        env = create_envelope(
            sub_type="state",
            sub_folder="udmi",
            transaction_id=self.handshake_tid,
            source=self.principal,
            principal=self.principal
        )
            
        payload = create_payload("udmi", udmi_payload)
        self.transport.publish(env, payload)

    def run(self):
        self.transport.connect()
        
        # Subscribe to handshake reply and discovery
        if self.conn_spec.protocol == "mqtt":
            # New unified topics: /uufi/c/...
            prefix = self.conn_spec.prefix + '/' if self.conn_spec.prefix else ''
            self.transport.subscribe(f"/{prefix}uufi/c/config/udmi", self.on_message)
            self.transport.subscribe(f"/{prefix}uufi/c/config/cloud", self.on_message)
            self.transport.subscribe(f"/{prefix}uufi/r/+/d/+/c/state/update", self.on_message)
            self.transport.subscribe(f"/{prefix}uufi/r/+/d/+/c/config/cloud", self.on_message)
        else:
            self.transport.subscribe(self.on_message)

        self.transport.loop_start()
        
        self.handshake_start_time = time.time()
        last_handshake = 0
        try:
            while True:
                now = time.time()
                if not self.is_active:
                    if self.transport.is_connected:
                        if now - self.handshake_start_time > 60:
                            print("[butler] CRITICAL: Handshake timeout. Fail-fast.", flush=True)
                            sys.exit(1)
                        if now - last_handshake > 5:
                            self.send_handshake()
                            last_handshake = now
                    else:
                        # Reset handshake start time until we actually connect
                        self.handshake_start_time = now
                else:
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
