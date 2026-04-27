import json
import re
from butler.common import ButlerMQTTBase

class ButlerVerifier(ButlerMQTTBase):
    def __init__(self, host="localhost", port=1883):
        super().__init__(source="verifier", host=host, port=port)
        # device_id -> current_state
        self.device_states = {}
        # States: IDLE, UPDATING, PENDING

    def on_connect(self):
        print("Verifier connected. Subscribing to butler/+/status and butler/+/update_payload")
        self.subscribe("butler/+/status")
        self.subscribe("butler/+/update_payload")

    def on_message(self, topic, data):
        # Validate timestamp format
        timestamp = data.get("timestamp", "")
        if not re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", timestamp):
            source = data.get("source", "unknown")
            print(f"[{source}] REJECT: Invalid timestamp format: {timestamp}")
            # We don't have a specific device_id here if it's a general message, 
            # but we can try to extract it from topic or data.
            match = re.match(r"butler/([^/]+)/", topic)
            device_id = match.group(1) if match else "unknown"
            self._report(device_id, "fail", f"Invalid timestamp format from {source}: {timestamp}")
            return

        # butler/{device_id}/{type}
        match = re.match(r"butler/([^/]+)/([^/]+)", topic)
        if not match:
            return

        device_id = match.group(1)
        msg_type = match.group(2)
        
        current_internal_state = self.device_states.get(device_id, "IDLE")

        if msg_type == "update_payload":
            self.device_states[device_id] = "UPDATING"
            print(f"[{device_id}] Received update_payload. Moving to UPDATING state.")
            # No verification message yet, waiting for pending status

        elif msg_type == "status":
            device_reported_state = data.get("payload", {}).get("state")
            print(f"[{device_id}] Reported status: {device_reported_state} (Internal state: {current_internal_state})")

            if device_reported_state == "pending":
                if current_internal_state == "UPDATING":
                    self.device_states[device_id] = "PENDING"
                    self._report(device_id, "pass", "Device correctly transitioned to PENDING after update_payload")
                elif current_internal_state == "IDLE":
                    # Might have missed update_payload, but we allow it for robustness
                    self.device_states[device_id] = "PENDING"
                # If already PENDING, just ignore (duplicate status)

            elif device_reported_state in ["success", "failure"]:
                if current_internal_state == "PENDING":
                    self.device_states[device_id] = "IDLE"
                    self._report(device_id, "pass", f"Device correctly transitioned from PENDING to {device_reported_state.upper()}")
                else:
                    self._report(device_id, "fail", f"Invalid state transition: {device_reported_state.upper()} while in {current_internal_state} state")
                    self.device_states[device_id] = "IDLE"

            elif device_reported_state == "quiescent":
                # If we were in PENDING, we should have seen success/failure first, 
                # but sometimes we might miss it. 
                # However, if we are in IDLE, it's normal.
                if current_internal_state != "IDLE":
                    # If we jumped straight to quiescent from UPDATING or PENDING
                    self._report(device_id, "fail", f"Invalid state transition: QUIESCENT while in {current_internal_state} state")
                self.device_states[device_id] = "IDLE"

    def _report(self, device_id, result, message):
        print(f"[{device_id}] VERIFY {result.upper()}: {message}")
        self.publish(device_id, "verify", {"result": result, "message": message})

def main():
    verifier = ButlerVerifier()
    verifier.connect()
    try:
        verifier.loop_forever()
    except KeyboardInterrupt:
        print("\nStopping verifier...")

if __name__ == "__main__":
    main()
