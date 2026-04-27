import time
import argparse
import sys
import os
import threading
from butler.common import ButlerMQTTBase
from butler.model_repo import ModelRepository
from butler.blob_repo import BlobRepository

class ButlerOrchestrator(ButlerMQTTBase):
    def __init__(self, failure_mode=False):
        super().__init__(source="butler")
        self.model_repo = ModelRepository()
        self.blob_repo = BlobRepository()
        self.failure_mode = failure_mode
        self.pending_updates = {}  # device_id -> {'start_time': float, 'target_version': str}
        self.timeout_seconds = 60

    def on_connect(self):
        print("Orchestrator connected to bus.")
        self.subscribe("butler/+/status")

    def on_message(self, topic, data):
        if self.failure_mode:
            # In failure mode, we might just ignore everything or behave oddly.
            # Spec says "introduces a failure mode of some kind... does not progress to next state"
            return

        parts = topic.split('/')
        if len(parts) == 3 and parts[2] == "status":
            device_id = parts[1]
            self.handle_status(device_id, data)

    def handle_status(self, device_id, data):
        payload = data.get("payload", {})
        state = payload.get("state")
        version = payload.get("version")

        if state == "success":
            print(f"Device {device_id} reported success for version {version}")
            self.model_repo.update_current_version(device_id, version)
            if device_id in self.pending_updates:
                del self.pending_updates[device_id]
        
        elif state == "failure":
            print(f"Device {device_id} reported failure for version {version}")
            self.trigger_rollback(device_id)
            if device_id in self.pending_updates:
                del self.pending_updates[device_id]
        
        elif state == "pending":
            # Device acknowledged update
            if device_id in self.pending_updates:
                # Update start time to avoid premature timeout if it's still working?
                # Actually, 60s is for the whole process usually.
                pass

    def trigger_rollback(self, device_id):
        device_state = self.model_repo.get_device_state(device_id)
        if not device_state:
            return
        
        lkg = device_state.get("last_known_good")
        if lkg:
            print(f"Rolling back device {device_id} to LKG: {lkg}")
            self.model_repo.set_target_version(device_id, lkg)
        else:
            print(f"No LKG for device {device_id}, cannot rollback.")

    def check_for_updates(self):
        self.model_repo = ModelRepository() # Reload model
        for device_id in self.model_repo.get_all_devices():
            state = self.model_repo.get_device_state(device_id)
            target = state.get("target_version")
            current = state.get("current_version")

            if target and target != current:
                if device_id not in self.pending_updates:
                    self.initiate_update(device_id, state)
                else:
                    # Check for timeout
                    start_time = self.pending_updates[device_id]['start_time']
                    if time.time() - start_time > self.timeout_seconds:
                        print(f"Update for {device_id} timed out.")
                        self.trigger_rollback(device_id)
                        del self.pending_updates[device_id]

    def initiate_update(self, device_id, device_state):
        target_version = device_state.get("target_version")
        make = device_state.get("make")
        model = device_state.get("model")
        subsystem = device_state.get("subsystem")

        blob_path, sha256 = self.blob_repo.get_blob_info(make, model, subsystem, target_version)
        if not blob_path:
            print(f"Error: Blob not found for {make}/{model}/{subsystem} version {target_version}")
            return

        print(f"Initiating update for {device_id} to version {target_version}")
        payload = {
            "version": target_version,
            "url": f"file://{os.path.abspath(blob_path)}",
            "sha256": sha256
        }
        self.publish(device_id, "update_payload", payload)
        self.pending_updates[device_id] = {
            'start_time': time.time(),
            'target_version': target_version
        }

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("-f", "--failure", action="store_true", help="Enable failure mode")
    args = parser.parse_args()

    orchestrator = ButlerOrchestrator(failure_mode=args.failure)
    orchestrator.connect()
    orchestrator.loop_start()

    print("Butler Orchestrator running...")
    try:
        while True:
            orchestrator.check_for_updates()
            time.sleep(5)
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    import os
    main()
