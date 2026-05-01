import json
import re
from butler.common import ButlerMQTTBase

class ButlerVerifier(ButlerMQTTBase):
    def __init__(self):
        super().__init__(source="verifier")
        self.device_states = {}
        self.active_clients = set()
        self.rfc3339_regex = re.compile(
            r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$'
        )

    def on_connect(self):
        print("[verifier] Verifier connected, observing traffic...")
        # Verifier just listens to all UUFI traffic
        self.subscribe_uufi()

    def validate_message(self, data):
        # Validation Schema: mandatory UDMI payload fields
        # Note: data is already the merged envelope + payload
        errors = []
        
        if "timestamp" not in data:
            errors.append("Missing mandatory 'timestamp' in payload")
        elif not self.rfc3339_regex.match(data["timestamp"]):
            errors.append(f"Invalid timestamp format: {data['timestamp']} (expected RFC 3339)")
            
        if "version" not in data:
            errors.append("Missing mandatory 'version' in payload")
            
        return errors

    def on_message(self, topic, device_id, sub_type, sub_folder, data):
        source = data.get("source")
        
        # Validation
        val_errors = self.validate_message(data)
        if val_errors:
            for err in val_errors:
                self.report_verification(device_id or "unknown", f"VALIDATION ERROR: {err}", level="ERROR")

        # Handshake Awareness
        if "/uufi/c/" in topic and sub_type == "state" and sub_folder == "udmi":
            self.report_verification(device_id or source, f"Handshake started by {source}")
        
        if sub_type == "config" and sub_folder == "udmi":
            udmi = data.get("udmi", {})
            if "reply" in udmi:
                client = udmi["reply"].get("msg_source")
                if client:
                    self.active_clients.add(client)
                    self.report_verification(device_id or client, f"Handshake completed for {client}")

        # State Transition Monitoring
        if sub_type == "state" and sub_folder == "update":
            status = data.get("status", "quiescent")
            old_status = self.device_states.get(device_id, "quiescent")
            
            if old_status != status:
                self.report_verification(device_id, f"State transition: {old_status} -> {status}")
                
                # Validate transitions
                if old_status == "quiescent" and status not in ["pending", "quiescent"]:
                    self.report_verification(device_id, f"INVALID TRANSITION: {old_status} -> {status}", level="ERROR")
                
                self.device_states[device_id] = status
            
        elif sub_type == "config" and sub_folder == "update":
            self.report_verification(device_id, f"Update config sent to {device_id}")
            
        elif sub_type == "model" and sub_folder == "cloud":
            operation = data.get("operation")
            self.report_verification(device_id, f"Cloud model updated for {device_id}: {operation}")

    def report_verification(self, device_id, message, level="INFO"):
        print(f"VERIFIER [{level}]: {message}")
        payload = {
            "message": message,
            "level": level,
            "device_id": device_id,
            "timestamp": data_now()
        }
        self.client.publish("butler/verify", json.dumps(payload))

def data_now():
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

def main():
    verifier = ButlerVerifier()
    verifier.connect()
    verifier.loop_forever()

if __name__ == "__main__":
    main()
