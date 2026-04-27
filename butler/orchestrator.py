import paho.mqtt.client as mqtt
import json
import time
import sys
import argparse
from butler.model_repo import ModelRepository
from butler.blob_repo import BlobRepository
from butler.messaging import create_message, parse_message

class Orchestrator:
    def __init__(self, fail_mode=False):
        self.model_repo = ModelRepository()
        self.blob_repo = BlobRepository()
        self.fail_mode = fail_mode
        self.client = mqtt.Client()
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        self.pending_updates = {} # device_id: {timestamp, target_version, subsystem}
        self.seen_nonces = set()

    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            client.subscribe("butler/+/status")
        else:
            print(f"Orchestrator failed to connect: {rc}")

    def on_message(self, client, userdata, msg):
        message = parse_message(msg.payload)
        if not message or message.get("type") != "status":
            return

        nonce = message.get("nonce")
        if nonce in self.seen_nonces:
            return
        self.seen_nonces.add(nonce)
        if len(self.seen_nonces) > 1000:
            self.seen_nonces.clear()

        device_id = msg.topic.split('/')[1]
        payload = message.get("payload", {})
        subsystem = payload.get("subsystem", "main")
        state = payload.get("state")
        current_version = payload.get("current_version")

        print(f"[butler] Status from {device_id}: {state} ({current_version})")

        if state == "success" or state == "quiescent":
            self.model_repo.update_current_version(device_id, subsystem, current_version)
            if state == "success" and device_id in self.pending_updates:
                del self.pending_updates[device_id]
        elif state == "failure":
            print(f"[butler] Device {device_id} reported FAILURE. Rolling back...")
            self.model_repo.rollback(device_id, subsystem)
            if device_id in self.pending_updates:
                del self.pending_updates[device_id]
        elif state == "pending":
            if device_id in self.pending_updates:
                # Refresh timeout? Spec says treat as failure if no success/failure within window.
                # So we just let the timer run.
                pass

    def check_reconciliation(self):
        self.model_repo.reload()
        devices = self.model_repo.get_all_devices()
        for device_id, subsystems in devices.items():
            for subsystem, info in subsystems.items():
                target = info.get("target_version")
                current = info.get("current_version")
                
                if target != current and device_id not in self.pending_updates:
                    print(f"[butler] Reconciliation triggered for {device_id}: {current} -> {target}")
                    
                    if self.fail_mode:
                        print(f"[butler] FAILURE MODE: Ignoring reconciliation.")
                        continue

                    metadata = self.blob_repo.get_blob_metadata(
                        info.get("make"), info.get("model"), subsystem, target
                    )
                    
                    if metadata:
                        payload = {
                            "version": target,
                            "url": metadata["url"],
                            "sha256": metadata["sha256"]
                        }
                        msg = create_message(source="butler", destination=device_id, 
                                           msg_type="update_payload", payload=payload)
                        self.client.publish(f"butler/{device_id}/update_payload", json.dumps(msg))
                        self.pending_updates[device_id] = {
                            "timestamp": time.time(),
                            "target_version": target,
                            "subsystem": subsystem
                        }
                    else:
                        print(f"[butler] No blob metadata found for {target}")

    def check_timeouts(self):
        now = time.time()
        to_remove = []
        for device_id, info in self.pending_updates.items():
            if now - info["timestamp"] > 60: # Default timeout
                print(f"[butler] Timeout for {device_id}. Rolling back...")
                self.model_repo.rollback(device_id, info["subsystem"])
                to_remove.append(device_id)
        
        for d in to_remove:
            del self.pending_updates[d]

    def run(self):
        self.client.connect("localhost", 1883, 60)
        self.client.loop_start()
        
        try:
            while True:
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
