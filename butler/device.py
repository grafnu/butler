import json
import time
import sys
import os
import hashlib
import argparse
from butler.messaging import create_payload, create_envelope
from butler.model_repo import ModelRepository
from butler.conn_spec import parse_conn_spec
from butler.transport import get_transport

class MockDevice:
    def __init__(self, conn_spec, device_id, fail_mode=False):
        self.conn_spec = conn_spec
        self.device_id = device_id
        self.fail_mode = fail_mode
        self.current_version = "0.0.0" 
        self.state = "quiescent"
        self.transport = get_transport(conn_spec)
        self.subsystem = "main"
        self.seen_nonces = set()
        self.model_repo = ModelRepository()
        self.registry_id = "butler-registry"
        
        # Initialize state from model repo if available
        dev_info = self.model_repo.get_device_state(self.device_id, self.subsystem)
        if dev_info:
            self.current_version = dev_info.get("current_version", "0.0.0")
            print(f"[mocket] Initialized {self.device_id} current_version to {self.current_version}", flush=True)
        else:
            self.current_version = "0.0.0"

    def on_message(self, env, payload, topic, raw=None):
        if not payload: return
        sub_type = env.get("subType")
        sub_folder = env.get("subFolder")
        device_id = env.get("deviceId")
        principal = env.get("principal")

        # UUFI handshake state from clients
        if sub_type == "state" and sub_folder == "udmi" and principal:
            self.handle_handshake(principal, payload, env.get("transactionId"))
            return

        # Cloud ops
        if device_id == self.registry_id and sub_folder == "cloud":
            self.handle_cloud_op(sub_type, payload)
            return

        # Update config
        if device_id == self.device_id and sub_type == "config" and sub_folder == "update":
            nonce = env.get("nonce")
            if nonce in self.seen_nonces: return
            self.seen_nonces.add(nonce)
            if len(self.seen_nonces) > 1000: self.seen_nonces.clear()

            update = payload.get("update", {})
            url = update.get("url")
            sha256 = update.get("sha256")
            version = update.get("version")

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
                    self.report_status()
                    return

                with open(url, "rb") as f:
                    actual_hash = hashlib.sha256(f.read()).hexdigest()

                if actual_hash == sha256:
                    time.sleep(1)
                    self.current_version = version
                    self.state = "success"
                    self.report_status()
                    self.state = "quiescent"
                else:
                    print(f"[mocket] Hash mismatch for {url}: expected {sha256}, got {actual_hash}", flush=True)
                    self.state = "failure"
                    self.report_status()
            except Exception as e:
                print(f"[mocket] Error applying update: {e}", flush=True)
                self.state = "failure"
                self.report_status()

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
            registry_id="",
            device_id="",
            sub_type="config",
            sub_folder="udmi",
            transaction_id=tid,
            source="mockit"
        )
        env["principal"] = principal

        payload = create_payload("udmi", reply_payload_data)
        self.transport.publish(env, payload)
        self.push_model()

    def handle_cloud_op(self, sub_type, payload):
        cloud = payload.get("cloud", payload)
        operation = cloud.get("operation")

        if sub_type == "query" and operation == "READ":
            self.push_model()
        elif sub_type == "model" and operation == "UPDATE":
            devices = cloud.get("devices", {})
            for dev_id, subsystems in devices.items():
                if not isinstance(subsystems, dict): continue
                for subsystem, info in subsystems.items():
                    if not isinstance(info, dict): continue
                    target = info.get("target_version")
                    current = info.get("current_version")

                    if target is not None:
                        self.model_repo.set_target_version(dev_id, subsystem, target)
                    if current is not None:
                        self.model_repo.update_current_version(dev_id, subsystem, current)
            self.push_model()

    def push_model(self):
        self.model_repo.reload()
        model_payload_data = self.model_repo.data # This contains {"devices": {...}}
        env = create_envelope(
            registry_id=self.registry_id,
            device_id=self.registry_id,
            sub_type="config",
            sub_folder="cloud",
            source="mockit"
        )
        payload = create_payload("cloud", model_payload_data)
        self.transport.publish(env, payload)

    def report_status(self):
        update_data = {
            "current_version": self.current_version,
            "state": self.state,
            "subsystem": self.subsystem
        }
        env = create_envelope(
            registry_id=self.registry_id,
            device_id=self.device_id,
            sub_type="state",
            sub_folder="update",
            source="mockit"
        )
        payload = create_payload("update", update_data)
        self.transport.publish(env, payload)

    def run(self):
        self.transport.connect()
        
        if self.conn_spec.protocol == "mqtt":
            self.transport.subscribe(f"/uufi/{self.conn_spec.prefix + '/' if self.conn_spec.prefix else ''}r/{self.registry_id}/d/{self.device_id}/config/update", self.on_message)
            self.transport.subscribe(f"/uufi/{self.conn_spec.prefix + '/' if self.conn_spec.prefix else ''}p/+/state/udmi", self.on_message)
            self.transport.subscribe(f"/uufi/{self.conn_spec.prefix + '/' if self.conn_spec.prefix else ''}r/{self.registry_id}/d/{self.registry_id}/query/cloud", self.on_message)
            self.transport.subscribe(f"/uufi/{self.conn_spec.prefix + '/' if self.conn_spec.prefix else ''}r/{self.registry_id}/d/{self.registry_id}/model/cloud", self.on_message)
        else:
            self.transport.subscribe(self.on_message)
            
        self.transport.loop_start()
        
        last_push = 0
        try:
            while True:
                now = time.time()
                if now - last_push > 5:
                    self.push_model()
                    last_push = now
                self.report_status()
                time.sleep(10)
        except KeyboardInterrupt:
            self.transport.loop_stop()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("args", nargs="+", help="[conn_spec] device_id")
    parser.add_argument("-f", action="store_true", help="Enable failure mode")
    parsed_args = parser.parse_args()

    if len(parsed_args.args) == 1:
        conn_str = None
        device_id = parsed_args.args[0]
    else:
        conn_str = parsed_args.args[0]
        device_id = parsed_args.args[1]

    conn_spec = parse_conn_spec(conn_str, differentiator="mocket")
    device = MockDevice(conn_spec, device_id, fail_mode=parsed_args.f)
    print(f"Starting mock device {device_id} with {conn_spec}...")
    device.run()

if __name__ == "__main__":
    main()
