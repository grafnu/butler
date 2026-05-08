import json
import time
import sys
import os
import hashlib
import argparse
import datetime
from butler.messaging import create_payload, create_envelope
from butler.model_repo import ModelRepository
from butler.conn_spec import parse_conn_spec
from butler.transport import get_transport

class MockDevice:
    def __init__(self, conn_spec, registry_id, device_id, fail_mode=False):
        self.conn_spec = conn_spec
        self.registry_id = registry_id
        self.device_id = device_id
        self.fail_mode = fail_mode
        self.current_version = "0.0.0" 
        self.lkg_version = "0.0.0"
        self.state = "quiescent"
        self.transport = get_transport(conn_spec)
        self.subsystem = "main"
        self.nonce_history = {} # nonce: timestamp
        self.model_repo = ModelRepository()
        
        # Initialize state from model repo if available
        dev_info = self.model_repo.get_device_state(self.registry_id, self.device_id, self.subsystem)
        if dev_info:
            self.current_version = dev_info.get("current_version", "0.0.0")
            self.lkg_version = dev_info.get("lkg_version", "0.0.0")
            print(f"[mocket] Initialized {self.device_id} current_version to {self.current_version}, lkg to {self.lkg_version}", flush=True)
        else:
            self.current_version = "0.0.0"
            self.lkg_version = "0.0.0"

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

        # UUFI handshake state from clients
        if sub_type == "state" and sub_folder == "udmi" and not device_id:
            self.handle_handshake(principal, payload, env.get("transactionId"))
            return

        # Cloud ops
        if sub_folder == "cloud" and (
            (registry_id == self.registry_id and device_id == self.registry_id) or
            (not registry_id and not device_id) # Discovery query
        ):
            self.handle_cloud_op(sub_type, payload)
            return

        # Update config
        if registry_id == self.registry_id and device_id == self.device_id and sub_type == "config" and sub_folder == "update":
            update = payload.get("update", {})
            url = update.get("url")
            sha256 = update.get("sha256")
            version = update.get("version")
            subsystem = update.get("subsystem", "main")

            if not version: return

            print(f"[mocket] Device {self.device_id} received update to {version}", flush=True)
            self.state = "pending"
            self.report_status()

            if self.fail_mode:
                print(f"[mocket] FAILURE MODE: Not progressing from pending.", flush=True)
                return

            time.sleep(1)
            try:
                if not os.path.exists(url):
                    print(f"[mocket] Blob not found: {url}", flush=True)
                    self.state = "failure"
                    self.report_status(category="blob_invalid")
                    return

                with open(url, "rb") as f:
                    actual_hash = hashlib.sha256(f.read()).hexdigest()

                if actual_hash == sha256:
                    time.sleep(1)
                    self.current_version = version
                    self.lkg_version = version
                    self.state = "success"
                    self.report_status()
                    self.state = "quiescent"
                else:
                    print(f"[mocket] Hash mismatch for {url}: expected {sha256}, got {actual_hash}", flush=True)
                    self.state = "failure"
                    self.report_status(category="blob_invalid")
            except Exception as e:
                print(f"[mocket] Error applying update: {e}", flush=True)
                self.state = "failure"
                self.report_status(category="apply_error")

    def handle_handshake(self, principal, payload, tid):
        udmi = payload.get("udmi", payload)
        setup = udmi.get("setup", {})
        
        print(f"[mocket] Received UUFI handshake state from {principal} (tid: {tid})", flush=True)

        reply_payload_data = {
            "setup": {
                "functions_min": 9,
                "functions_max": 9,
                "udmi_version": "1.5.2"
            },
            "reply": setup
        }

        env = create_envelope(
            sub_type="config",
            sub_folder="udmi",
            transaction_id=tid,
            source="mocket",
            principal=principal
        )

        payload = create_payload("udmi", reply_payload_data)
        self.transport.publish(env, payload)
        self.push_model()

    def handle_cloud_op(self, sub_type, payload):
        cloud = payload.get("cloud", payload)
        operation = cloud.get("operation")

        if sub_type == "query" and operation == "READ":
            self.push_model()
        elif sub_type == "model" and operation == "UPDATE":
            registries = cloud.get("registries", {})
            for reg_id, reg_data in registries.items():
                devices = reg_data.get("devices", {})
                for dev_id, subsystems in devices.items():
                    if not isinstance(subsystems, dict): continue
                    for subsystem, info in subsystems.items():
                        if not isinstance(info, dict): continue
                        target = info.get("target_version")
                        current = info.get("current_version")
                        lkg = info.get("lkg_version")

                        if target is not None:
                            self.model_repo.set_target_version(reg_id, dev_id, subsystem, target)
                        if current is not None:
                            self.model_repo.update_current_version(reg_id, dev_id, subsystem, current, lkg_version=lkg)
            self.push_model()

    def push_model(self):
        self.model_repo.reload()
        # Ensure data is wrapped in registries map
        model_data = self.model_repo.data
        env = create_envelope(
            sub_type="config",
            sub_folder="cloud",
            source="mocket"
        )
        payload = create_payload("cloud", model_data)
        self.transport.publish(env, payload)

    def report_status(self, category=None):
        update_data = {
            "current_version": self.current_version,
            "lkg_version": self.lkg_version,
            "state": self.state,
            "subsystem": self.subsystem
        }
        if category:
            update_data["category"] = category
        env = create_envelope(
            registry_id=self.registry_id,
            device_id=self.device_id,
            sub_type="state",
            sub_folder="update",
            source="mocket"
        )
        payload = create_payload("update", update_data)
        self.transport.publish(env, payload)

    def run(self):
        self.transport.connect()
        
        if self.conn_spec.protocol == "mqtt":
            prefix = self.conn_spec.prefix + '/' if self.conn_spec.prefix else ''
            # Generic UUFI channel for handshakes and discovery
            self.transport.subscribe(f"/{prefix}uufi/c/+/+", self.on_message)
            # Registry-specific channel
            self.transport.subscribe(f"/{prefix}uufi/r/{self.registry_id}/d/{self.device_id}/c/+/+", self.on_message)
            # Registry-level cloud ops
            self.transport.subscribe(f"/{prefix}uufi/r/{self.registry_id}/d/{self.registry_id}/c/+/+", self.on_message)
        else:
            self.transport.subscribe(self.on_message)
            
        self.transport.loop_start()
        
        last_push = 0
        last_status = 0
        try:
            while True:
                now = time.time()
                if now - last_push > 5:
                    self.push_model()
                    last_push = now
                
                if now - last_status > 15:
                    self.report_status()
                    last_status = now
                
                time.sleep(1)
        except KeyboardInterrupt:
            self.transport.loop_stop()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("conn_spec", help="Connection spec URL")
    parser.add_argument("registry_id", help="Registry ID")
    parser.add_argument("device_id", help="Device ID")
    parser.add_argument("-f", action="store_true", help="Enable failure mode")
    args = parser.parse_args()

    conn_spec = parse_conn_spec(args.conn_spec, differentiator="mocket")
    device = MockDevice(conn_spec, args.registry_id, args.device_id, fail_mode=args.f)
    print(f"Starting mock device {args.device_id} in {args.registry_id} with {conn_spec}...")
    device.run()

if __name__ == "__main__":
    main()
