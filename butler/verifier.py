import json
from butler.common import ButlerMQTTBase

class ButlerVerifier(ButlerMQTTBase):
    def __init__(self):
        super().__init__(source="verifier")
        self.device_states = {} # device_id -> last_status

    def on_connect(self):
        print("[verifier] Verifier connected, acting as System proxy")
        # System subscribes to all reflects to relay them
        self.client.subscribe("udmi/reflect/#")

    def on_message(self, topic, device_id, sub_type, sub_folder, data):
        payload = data.get("payload", {})
        source = data.get("source")
        
        # 1. Handle UUFI Handshake (System side)
        if sub_type == "state" and sub_folder == "udmi":
            self.handle_handshake_state(device_id, source, payload)
            return

        # 2. Relay reflect to reply (Simplified system behavior)
        # In UUFI, state reflects are often relayed to all interested clients
        # Config reflects are relayed to the target device
        if sub_type in ["state", "config"]:
            # Relay as reply
            self.publish_uufi(device_id, sub_type, payload, sub_folder, direction="reply")
            
            # Validation logic
            if sub_type == "state" and sub_folder == "update":
                status = payload.get("status")
                last_status = self.device_states.get(device_id)
                if last_status == "pending" and status == "quiescent":
                    # This might be an invalid transition if success/failure was skipped
                    self.report_verification(device_id, f"WARNING: {device_id} transitioned from pending to quiescent without reporting success/failure")
                self.device_states[device_id] = status
                self.report_verification(device_id, f"Device {device_id} state: {status}")

        # 3. Validation for config
        if sub_type == "config" and sub_folder == "update":
            self.report_verification(device_id, f"Update config sent to {device_id}")

    def handle_handshake_state(self, device_id, source, payload):
        udmi = payload.get("udmi", {})
        setup = udmi.get("setup", {})
        transaction_id = setup.get("transaction_id")
        
        print(f"[verifier] Handling handshake from {source}")
        
        response_payload = {
            "udmi": {
                "setup": {
                    "functions_min": 9,
                    "functions_max": 9,
                    "udmi_version": "1.5.2"
                },
                "reply": {
                    "functions_ver": 9,
                    "transaction_id": transaction_id,
                    "msg_source": source
                }
            }
        }
        self.publish_uufi(device_id, "config", response_payload, "udmi", direction="reply", target_source=source)

    def report_verification(self, device_id, message):
        print(f"VERIFIER: {message}")
        # Publish to canonical verify topic
        self.publish_uufi(device_id, "events", {"message": message}, "verify", direction="reply")

def main():
    verifier = ButlerVerifier()
    verifier.connect()
    verifier.loop_forever()

if __name__ == "__main__":
    main()
