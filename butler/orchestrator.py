import time
from butler.common import ButlerMQTTClient
from butler.model_repo import ModelRepository
from butler.blob_repo import BlobRepository

class ButlerOrchestrator:
    def __init__(self, host="localhost", port=1883, failure_mode=False):
        self.mqtt = ButlerMQTTClient("butler", host, port)
        self.model_repo = ModelRepository()
        self.blob_repo = BlobRepository()
        self.failure_mode = failure_mode
        self.mqtt.set_on_message(self.handle_message)
        self.device_states = {} # device_id -> current reported state (quiescent, pending, etc.)
        self.update_start_times = {} # device_id -> timestamp of last update push

    def start(self):
        self.mqtt.connect()
        # Subscribe to all device status topics
        self.mqtt.subscribe("butler/+/status")
        print("Butler started.")
        if self.failure_mode:
            print("[butler] FAILURE MODE ENABLED: Will skip model updates on device success.")

    def reconcile(self):
        # Proactively check model for mismatches
        mismatches = self.model_repo.get_all_mismatches()
        now = time.time()
        for mismatch in mismatches:
            device_id = mismatch["device_id"]
            subsystem = mismatch["subsystem"]
            target_state = mismatch["state"]
            
            # Check for timeout
            current_state = self.device_states.get(device_id, "quiescent")
            if current_state == "pending" and device_id in self.update_start_times:
                 if now - self.update_start_times[device_id] > 60: # 60 second timeout
                      print(f"[butler] Timeout detected for {device_id} on version {target_state['target_version']}")
                      # Trigger rollback on timeout as well
                      self.model_repo.rollback(device_id, subsystem)
                      del self.update_start_times[device_id]
                      continue

            # Simple reconciliation: if target version != current AND state is quiescent, push update.
            if target_state["target_version"] != target_state["current_version"] and current_state == "quiescent":
                 print(f"[butler] Reconciliation: Mismatch detected for {device_id}/{subsystem}")
                 self.push_update(device_id, subsystem, target_state)

    def stop(self):
        self.mqtt.disconnect()

    def handle_message(self, topic, data):
        parts = topic.split('/')
        if len(parts) < 2: return
        device_id = parts[1]
        msg_type = data["type"]
        payload = data["payload"]
        
        if msg_type == "status":
            self.process_status(device_id, payload)

    def process_status(self, device_id, payload):
        subsystem = payload.get("subsystem", "main")
        current_version = payload.get("current_version")
        state = payload.get("state")
        self.device_states[device_id] = state
        # print(f"[butler] Processing status for {device_id}: version={current_version}, state={state}")
        
        # Update current state in model repo
        if state == "success":
             if self.failure_mode:
                 print(f"[butler] Failure mode: skipping model update for {device_id} success.")
             else:
                 print(f"[butler] Update successful for {device_id}")
                 self.model_repo.update_current_version(device_id, subsystem, current_version)
        
        if state == "failure":
             print(f"[butler] Device {device_id} reported failure. Triggering rollback.")
             self.model_repo.rollback(device_id, subsystem)

        # Check for mismatch
        target_state = self.model_repo.get_device_state(device_id, subsystem)
        if not target_state:
             return

        if target_state["target_version"] != current_version and state == "quiescent":
             self.push_update(device_id, subsystem, target_state)
        elif target_state["target_version"] == current_version:
             pass
        else:
             pass

    def push_update(self, device_id, subsystem, target_state):
        make = target_state["make"]
        model = target_state["model"]
        version = target_state["target_version"]
        
        blob_info = self.blob_repo.get_blob_info(make, model, subsystem, version)
        if not blob_info:
             print(f"Blob not found for {make}/{model}/{subsystem}/{version}")
             return
        
        update_payload = {
            "subsystem": subsystem,
            "version": version,
            "url": blob_info["url"],
            "sha256": blob_info["sha256"]
        }
        
        print(f"Pushing update to {device_id}: {version}")
        self.update_start_times[device_id] = time.time()
        self.mqtt.publish(f"butler/{device_id}/update_payload", device_id, "update_payload", update_payload)

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Butler Orchestrator")
    parser.add_argument("-f", "--failure", action="store_true", help="Enable failure mode")
    args = parser.parse_args()

    orchestrator = ButlerOrchestrator(failure_mode=args.failure)
    orchestrator.start()
    try:
        while True:
            orchestrator.reconcile()
            time.sleep(5) # Reconcile every 5 seconds
    except KeyboardInterrupt:
        orchestrator.stop()

if __name__ == "__main__":
    main()
