import json
from butler.common import ButlerMQTTBase

class ButlerVerifier(ButlerMQTTBase):
    def __init__(self):
        super().__init__(source="verifier")
        self.device_states = {}

    def on_connect(self):
        print("[verifier] Verifier connected, observing traffic...")
        # Verifier just listens to all replies and reflects
        self.client.subscribe("udmi/#")

    def on_message(self, topic, device_id, sub_type, sub_folder, data):
        # Observational only
        if sub_type == "state" and sub_folder == "update":
            status = data.get("status")
            self.report_verification(device_id, f"Device {device_id} state: {status}")
            
        elif sub_type == "config" and sub_folder == "update":
            self.report_verification(device_id, f"Update config sent to {device_id}")
            
        elif sub_type == "model" and sub_folder == "cloud":
            operation = data.get("operation")
            self.report_verification(device_id, f"Cloud model updated for {device_id}: {operation}")

    def report_verification(self, device_id, message):
        print(f"VERIFIER: {message}")
        payload = {"message": message}
        # Assuming we can publish to a top-level topic 'verify'
        self.client.publish("verify", json.dumps(payload))

def main():
    verifier = ButlerVerifier()
    verifier.connect()
    verifier.loop_forever()

if __name__ == "__main__":
    main()
