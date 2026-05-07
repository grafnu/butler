import json
import re
import argparse
import threading
import time
from butler.common import ButlerBusFactory, get_default_conn_spec

class ButlerVerifier:
    def __init__(self, conn_spec=None):
        conn_spec = conn_spec or get_default_conn_spec()
        self.bus = ButlerBusFactory(source="verifier", conn_spec=conn_spec)
        self.connect = self.bus.connect
        self.loop_forever = self.bus.loop_forever
        self.subscribe_uufi = self.bus.subscribe_uufi
        
        self.bus.on_connect = self.on_connect
        self.bus.on_message = self.on_message
        
        self.device_states = {}
        self.active_clients = set()
        # Minimal precision format: YYYY-MM-DDTHH:MM:SSZ
        self.rfc3339_regex = re.compile(
            r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$'
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
        # Pattern: /uufi/p/{principal}/{subType}/{subFolder}
        is_handshake_topic = "/uufi/p/" in topic
        if is_handshake_topic and sub_type == "state" and sub_folder == "udmi":
            self.report_verification(device_id or source, f"Handshake started by {source}")
        
        if sub_type == "config" and sub_folder == "udmi":
            # Handshake reply is in 'udmi' subfolder
            udmi = data.get("udmi", {})
            if "reply" in udmi:
                client = udmi["reply"].get("msg_source")
                if client:
                    self.active_clients.add(client)
                    self.report_verification(device_id or client, f"Handshake completed for {client}")

        # State Transition Monitoring
        if sub_type == "state" and sub_folder == "update":
            # Device state is in 'update' subfolder
            update_state = data.get("update", {})
            status = update_state.get("status", "quiescent")
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
            # Cloud model data is in 'cloud' subfolder
            cloud_data = data.get("cloud", {})
            operation = cloud_data.get("operation")
            self.report_verification(device_id, f"Cloud model updated for {device_id}: {operation}")

    def report_verification(self, device_id, message, level="INFO"):
        print(f"VERIFIER [{level}]: {message}")
        import datetime
        timestamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        payload = {
            "message": message,
            "level": level,
            "device_id": device_id,
            "timestamp": timestamp
        }
        self.bus.publish("butler/verify", json.dumps(payload))

def data_now():
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("conn_spec", help="Connection specification")
    args = parser.parse_args()
    
    verifier = ButlerVerifier(conn_spec=args.conn_spec)
    verifier.connect()
    threading.Thread(target=verifier.loop_forever, daemon=True).start()
    while True:
        time.sleep(1)

if __name__ == "__main__":
    main()
