import paho.mqtt.client as mqtt
import json
import time
import sys
import os
import argparse
from butler.blob_repo import BlobRepository
from butler.messaging import create_uufi_message, parse_uufi_message

class Orchestrator:
    def __init__(self, fail_mode=False):
        self.blob_repo = BlobRepository()
        self.fail_mode = fail_mode
        self.client = mqtt.Client()
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        self.pending_updates = {} # device_id: {timestamp, target_version, subsystem}
        self.seen_nonces = set()
        self.is_active = False
        self.handshake_tid = None
        self.registry_id = "butler-registry"
        self.model = {}

    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            client.subscribe(f"/uufi/r/{self.registry_id}/d/+/state/update")
            client.subscribe(f"/uufi/c/butler/config/udmi")
            client.subscribe(f"/uufi/r/{self.registry_id}/d/{self.registry_id}/config/cloud")
        else:
            print(f"Orchestrator failed to connect: {rc}", flush=True)

    def on_message(self, client, userdata, msg):
        message, envelope = parse_uufi_message(msg.payload)
        if not message:
            return

        parts = msg.topic.split('/')
        if len(parts) < 5:
            return
        
        if parts[2] == 'c' and parts[3] == 'butler':
            sub_type = parts[4]
            sub_folder = parts[5]
            if sub_type == "config" and sub_folder == "udmi":
                self.handle_handshake_reply(message)
            return

        if len(parts) < 8:
            return

        device_id = parts[5]
        sub_type = parts[6]
        sub_folder = parts[7]

        if device_id == self.registry_id and sub_folder == "cloud" and sub_type == "config":
            # message is {"cloud": {"devices": {...}}}
            self.model = message.get("cloud", {})
            print(f"[butler] Received model update from cloud", flush=True)
            return

        nonce = message.get("nonce")
        if nonce in self.seen_nonces:
            return
        self.seen_nonces.add(nonce)
        if len(self.seen_nonces) > 1000:
            self.seen_nonces.clear()

        if sub_type == "state" and sub_folder == "update":
            update = message.get("update", {})
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

    def handle_handshake_reply(self, message):
        udmi = message.get("udmi", {})
        reply = udmi.get("reply", {})
        tid = reply.get("transaction_id")
        if tid == self.handshake_tid:
            print(f"[butler] UUFI Handshake complete (tid: {tid}). Orchestrator is ACTIVE.", flush=True)
            self.is_active = True
            self.query_cloud_model()

    def query_cloud_model(self):
        msg = create_uufi_message(
            registry_id=self.registry_id,
            device_id=self.registry_id,
            sub_type="query",
            sub_folder="cloud",
            payload={"operation": "READ"},
            source="butler"
        )
        topic = f"/uufi/r/{self.registry_id}/d/{self.registry_id}/query/cloud"
        self.client.publish(topic, json.dumps(msg))

    def update_cloud_model(self, device_id, subsystem, target_version=None, current_version=None):
        payload = {"operation": "UPDATE", "device_id": device_id, "subsystem": subsystem}
        if target_version: payload["target_version"] = target_version
        if current_version: payload["current_version"] = current_version
        
        msg = create_uufi_message(
            registry_id=self.registry_id,
            device_id=self.registry_id,
            sub_type="model",
            sub_folder="cloud",
            payload=payload,
            source="butler"
        )
        topic = f"/uufi/r/{self.registry_id}/d/{self.registry_id}/model/cloud"
        self.client.publish(topic, json.dumps(msg))

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
            if not isinstance(subsystems, dict):
                continue
            for subsystem, info in subsystems.items():
                if not isinstance(info, dict):
                    continue
                target = info.get("target_version")
                current = info.get("current_version")
                
                if target != current and device_id not in self.pending_updates:
                    print(f"[butler] Reconciliation triggered for {device_id}: {current} -> {target}", flush=True)
                    
                    if self.fail_mode:
                        continue

                    metadata = self.blob_repo.get_blob_metadata(
                        info.get("make"), info.get("model"), subsystem, target
                    )
                    
                    if metadata:
                        update_payload = {
                            "version": target,
                            "url": metadata["url"],
                            "sha256": metadata["sha256"]
                        }
                        msg = create_uufi_message(
                            registry_id=self.registry_id,
                            device_id=device_id,
                            sub_type="config",
                            sub_folder="update",
                            payload=update_payload,
                            source="butler"
                        )
                        topic = f"/uufi/r/{self.registry_id}/d/{device_id}/config/update"
                        self.client.publish(topic, json.dumps(msg))
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
                "msg_source": "butler",
                "user": "butler"
            }
        }
        msg = create_uufi_message(
            registry_id=self.registry_id,
            device_id="butler",
            sub_type="state",
            sub_folder="udmi",
            payload=udmi_payload,
            transaction_id=self.handshake_tid,
            source="butler"
        )
        topic = f"/uufi/c/butler/state/udmi"
        self.client.publish(topic, json.dumps(msg))

    def run(self):
        host = "localhost"
        port = 1883
        print(f"Orchestrator connecting to MQTT broker at {host}:{port}", flush=True)
        self.client.connect(host, port, 60)
        self.client.loop_start()
        
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
            self.client.loop_stop()
        
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
            self.client.loop_stop()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("-f", action="store_true", help="Enable failure mode")
    args = parser.parse_args()

    orchestrator = Orchestrator(fail_mode=args.f)
    print("Starting Butler Orchestrator...")
    orchestrator.run()

if __name__ == "__main__":
    main()
