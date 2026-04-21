import time
import json
from butler.common import ButlerMQTTClient

class ButlerVerifier:
    def __init__(self, host="localhost", port=1883):
        self.mqtt = ButlerMQTTClient("verifier", host, port)
        self.mqtt.set_on_message(self.handle_message)
        self.device_states = {} # device_id -> tracking info

    def start(self):
        self.mqtt.connect()
        self.mqtt.subscribe("butler/+/status")
        self.mqtt.subscribe("butler/+/update_payload")
        print("Butler Verifier started.")

    def stop(self):
        self.mqtt.disconnect()

    def report_verification(self, device_id, result, message):
        payload = {
            "result": result, # PASS or FAIL
            "message": message
        }
        print(f"[VERIFIER] {device_id}: {result} - {message}")
        self.mqtt.publish(f"butler/{device_id}/verify", device_id, "verify_result", payload)

    def handle_message(self, topic, data):
        parts = topic.split('/')
        device_id = parts[1]
        msg_type = data["type"]
        payload = data["payload"]

        if msg_type == "update_payload":
            self.handle_update_payload(device_id, payload)
        elif msg_type == "status":
            self.handle_status(device_id, payload)

    def handle_update_payload(self, device_id, payload):
        target_version = payload["version"]
        if device_id not in self.device_states:
            self.device_states[device_id] = {}
        
        tracking = self.device_states[device_id]
        
        if tracking.get("current_state") == "awaiting_rollback":
            expected_lkg = tracking.get("lkg")
            if target_version == expected_lkg:
                self.report_verification(device_id, "PASS", f"Correctly triggered rollback to LKG version {target_version}")
            else:
                self.report_verification(device_id, "FAIL", f"Rollback triggered but to version {target_version}, expected LKG {expected_lkg}")
        
        tracking["target_version"] = target_version
        tracking["current_state"] = "update_initiated"
        tracking["sequence"] = ["update_initiated"]
        print(f"[VERIFIER] Tracking update for {device_id} to version {target_version}")

    def handle_status(self, device_id, payload):
        state = payload["state"]
        current_version = payload["current_version"]
        
        if device_id not in self.device_states:
            # We track the last known good version seen on the bus
            if state == "quiescent":
                 self.device_states[device_id] = {"lkg": current_version, "current_state": "quiescent"}
            return

        tracking = self.device_states[device_id]
        
        if state == "pending":
            if tracking.get("current_state") == "update_initiated":
                tracking["current_state"] = "pending"
                tracking["sequence"].append("pending")
        
        elif state == "success":
            if tracking.get("current_state") == "pending":
                tracking["sequence"].append("success")
                if current_version == tracking.get("target_version"):
                    self.report_verification(device_id, "PASS", f"Successfully updated to {tracking['target_version']}")
                    # Update LKG
                    tracking["lkg"] = current_version
                    tracking["current_state"] = "quiescent"
                else:
                    self.report_verification(device_id, "FAIL", f"Reported success but version is {current_version}, expected {tracking.get('target_version')}")
                    del self.device_states[device_id]
            else:
                 # Unexpected success or already handled
                 pass

        elif state == "failure":
             if tracking.get("current_state") == "pending":
                 tracking["sequence"].append("failure")
                 self.report_verification(device_id, "PASS", f"Correctly reported failure for version {tracking.get('target_version')}")
                 tracking["current_state"] = "awaiting_rollback"
             else:
                 # Only report FAIL if we were actually expecting an update
                 if tracking.get("current_state") == "update_initiated":
                      self.report_verification(device_id, "FAIL", "Reported failure without pending state")
                      del self.device_states[device_id]

def main():
    verifier = ButlerVerifier()
    verifier.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        verifier.stop()

if __name__ == "__main__":
    main()
