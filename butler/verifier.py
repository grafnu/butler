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
        print("[verifier] Verifier connected, starting handshake...")
        self.bus.start_handshake()
        # Verifier just listens to all UUFI traffic
        self.subscribe_uufi()

    def main_loop(self):
        print("[verifier] Waiting for handshake completion...")
        if not self.bus.wait_for_handshake(timeout=60):
             print("[CRITICAL] Verifier handshake timed out after 60s. Exiting.", file=sys.stderr)
             sys.exit(1)
        
        print("[verifier] Handshake complete, performing validation duties.")
        while True:
            time.sleep(1)

    def validate_message(self, data, is_strict=False):
        # Validation Schema: mandatory UDMI payload fields
        errors = []
        
        timestamp = data.get("timestamp")
        if not timestamp:
            errors.append("Missing mandatory 'timestamp' in payload")
        elif is_strict:
            if not self.rfc3339_regex.match(timestamp):
                errors.append(f"Invalid timestamp format (STRICT): {timestamp} (expected minimal precision RFC 3339)")
        # Otherwise be permissive (if not strict) - we already checked presence
            
        if "version" not in data:
            errors.append("Missing mandatory 'version' in payload")
            
        return errors

    def on_message(self, topic, device_id, sub_type, sub_folder, data):
        source = data.get("source")
        registry_id = data.get("deviceRegistryId") or "unknown"
        
        # Validation strictness: Butler is strict, others are permissive
        is_strict = (source == "butler")
        val_errors = self.validate_message(data, is_strict=is_strict)
        if val_errors:
            for err in val_errors:
                self.report_verification(registry_id, device_id or "unknown", f"VALIDATION ERROR: {err}", level="ERROR")

        # Handshake Awareness: /uufi/c/ (registry-less)
        # In unified structure, handshake is just registry_id=None
        if not registry_id or registry_id == "unknown":
            if sub_type == "state" and sub_folder == "udmi":
                self.report_verification(registry_id, device_id or source, f"Handshake started by {source}")
            
            if sub_type == "config" and sub_folder == "udmi":
                udmi = data.get("udmi", {})
                if "reply" in udmi:
                    client = udmi["reply"].get("msg_source")
                    if client:
                        self.active_clients.add(client)
                        self.report_verification(registry_id, device_id or client, f"Handshake completed for {client}")

        # State Transition Monitoring
        if sub_type == "state" and sub_folder == "update":
            update_state = data.get("update", {})
            status = update_state.get("status", "quiescent")
            key = f"{registry_id}/{device_id}"
            old_status = self.device_states.get(key, "quiescent")
            
            if old_status != status:
                self.report_verification(registry_id, device_id, f"State transition: {old_status} -> {status}")
                
                # Validate transitions
                if old_status == "quiescent" and status not in ["pending", "quiescent"]:
                    self.report_verification(registry_id, device_id, f"INVALID TRANSITION: {old_status} -> {status}", level="ERROR")
                
                self.device_states[key] = status
            
        elif sub_type == "config" and sub_folder == "update":
            self.report_verification(registry_id, device_id, f"Update config sent to {device_id}")
            
        elif sub_type == "model" and sub_folder == "cloud":
            operation = data.get("cloud", {}).get("operation")
            self.report_verification(registry_id, device_id, f"Cloud model updated for {device_id}: {operation}")

    def report_verification(self, registry_id, device_id, message, level="INFO"):
        print(f"VERIFIER [{level}]: {message}")
        payload = {
            "message": message,
            "level": level,
            "device_id": device_id,
            "timestamp": data_now(),
            "version": "1.5.2"
        }
        # Publish to /uufi/r/{registry_id}/d/{device_id}/c/validation
        self.bus.publish_uufi(device_id, "validation", payload, "validation", registry_id=registry_id)

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
    verifier.main_loop()

if __name__ == "__main__":
    main()
