import paho.mqtt.client as mqtt
import json
import time
import sys
import argparse
import re
import datetime
import threading
from butler.messaging import create_payload, create_envelope
from butler.conn_spec import parse_conn_spec, match_principal
from butler.transport import get_transport

class Verifier:
    def __init__(self, conn_spec):
        self.conn_spec = conn_spec
        self.transport = get_transport(conn_spec)
        self.device_states = {} # (registry_id, device_id, subsystem): last_state
        self.handshakes = {} # principal: {tid, active}
        self.lock = threading.Lock()
        # Strict minimal precision format: 2026-05-01T22:32:17Z
        self.strict_ts_regex = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$')
        # Graceful format (permissive RFC 3339)
        self.graceful_ts_regex = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$')
        self.default_registry_id = "default"
        self.is_active = False
        self.handshake_tid = None
        self.handshake_start_time = None
        self.processed_transactions = {} # tid/nonce: timestamp

    def send_handshake(self):
        import secrets
        self.handshake_tid = secrets.token_hex(4)
        if self.handshake_start_time is None:
            self.handshake_start_time = time.time()
        
        # UUFI Section 9.3: Handshake started for {principal}
        self.handshakes[self.conn_spec.principal] = {"tid": self.handshake_tid, "active": False}
        self.log_verification("unknown", "verifier", f"Handshake started for {self.conn_spec.principal}")

        udmi_payload = {
            "setup": {
                "functions_ver": 9,
                "transaction_id": self.handshake_tid,
                "msg_source": self.conn_spec.username,
                "user": self.conn_spec.username
            }
        }
        env = create_envelope(
            sub_type="state",
            sub_folder="udmi",
            transaction_id=self.handshake_tid,
            source=self.conn_spec.username,
            principal=self.conn_spec.principal
        )
            
        payload = create_payload("udmi", udmi_payload)
        self.transport.publish(env, payload)

    def handle_handshake_reply(self, payload, tid):
        if "udmi" in payload:
            print(f"[verifier] PROTOCOL VIOLATION: Handshake wrapped inside 'udmi'. Rejecting.", flush=True)
            return
        if tid != self.handshake_tid:
            print(f"[verifier] Handshake reply transaction ID mismatch: expected {self.handshake_tid}, got {tid}. Rejecting.", flush=True)
            return
        reply = payload.get("reply", {})
        reply_tid = reply.get("transaction_id")
        if reply_tid == self.handshake_tid:
            print(f"[verifier] UUFI Handshake complete (tid: {reply_tid}). Verifier is ACTIVE.", flush=True)
            self.is_active = True

    def on_message(self, env, payload, topic, raw=None):
        if not payload: return
        
        with self.lock:
            source = env.get("source")
            tid = env.get("transactionId")
            nonce = env.get("nonce")
            sub_folder = env.get("subFolder")
            sub_type = env.get("subType")
            device_id = env.get("deviceId")
            registry_id = env.get("deviceRegistryId") or self.default_registry_id
            principal = env.get("principal")

            # Deduplication (UUFI Section 7.3 & 9): Use nonce if present, else transactionId
            # Handshake messages (udmi subfolder) should bypass deduplication.
            dedup_id = nonce or tid
            if dedup_id and sub_folder != "udmi":
                now = time.time()
                if dedup_id in self.processed_transactions:
                    if now - self.processed_transactions[dedup_id] < 300:
                        return
                self.processed_transactions[dedup_id] = now

            # Monitor Handshake on /uufi/c/ (UUFI Section 9.3)
            if sub_folder == "udmi" and not device_id:
                if "udmi" in payload:
                    self.log_verification(registry_id, device_id or "verifier", "PROTOCOL VIOLATION: Handshake payload blocks wrapped inside 'udmi' root sub-object", level="FAIL")
                    return
                if principal:
                    # Section 11: Principal Suffix Standardization
                    is_valid_principal = False
                    if "." in principal:
                        parts = principal.split(".")
                        suffix = parts[-1]
                        if suffix in ["setup", "butler", "verifier", "device", "smokeit"]:
                            is_valid_principal = True
                    if not is_valid_principal:
                        self.log_verification(registry_id, "verifier", f"PROTOCOL VIOLATION: Non-standard principal string or suffix: {principal}", level="FAIL")

                if sub_type == "state":
                    setup = payload.get("setup", {})
                    tid = setup.get("transaction_id")
                    if principal:
                        self.handshakes[principal] = {"tid": tid, "active": False}
                        # UUFI Section 9.1: Self-reporting uses unknown/verifier
                        self.log_verification("unknown", "verifier", f"Handshake started for {principal}")
                elif sub_type == "config":
                    reply = payload.get("reply", {})
                    tid = reply.get("transaction_id")
                    if principal in self.handshakes and self.handshakes[principal]["tid"] == tid:
                        self.handshakes[principal]["active"] = True
                        # UUFI Section 9.1: Self-reporting uses unknown/verifier
                        self.log_verification("unknown", "verifier", f"Handshake completed for {principal}")

            # UUFI handshake config from systems (self-handling)
            if sub_type == "config" and sub_folder == "udmi" and not device_id:
                # UUFI Section 2.2: principal MUST match Client's identity
                if principal and not match_principal(principal, self.conn_spec.principal):
                    return
                self.handle_handshake_reply(payload, env.get("transactionId"))
                return

            # Monitor cloud model updates and reject if non-compliant
            if sub_folder == "cloud" and sub_type == "model":
                if "cloud" in payload:
                    self.log_verification(registry_id or "unknown", "verifier", "PROTOCOL VIOLATION: Cloud model update wrapped inside 'cloud' root sub-object", level="FAIL")
                    return
            if sub_folder == "cloud" and sub_type == "config":
                self.log_verification(registry_id or "unknown", "verifier", "PROTOCOL VIOLATION: Cloud model update sourced from /uufi/c/config/cloud is prohibited", level="FAIL")
                return

            # Mandatory field validation
            if "timestamp" not in payload or "version" not in payload:
                self.log_verification(registry_id, device_id or "verifier", "VALIDATION ERROR: Missing mandatory UDMI fields (timestamp or version)", level="FAIL")
                return
            
            timestamp = payload.get("timestamp")
            # Strict for butler, graceful for everyone else
            if source == "butler":
                if not self.strict_ts_regex.match(timestamp):
                    self.log_verification(registry_id, device_id or "verifier", f"VALIDATION ERROR: Butler emitted non-strict timestamp: {timestamp}", level="FAIL")
                    return
            else:
                if not self.graceful_ts_regex.match(timestamp):
                    self.log_verification(registry_id, device_id or "verifier", f"VALIDATION ERROR: Invalid timestamp format from {source}: {timestamp}", level="FAIL")
                    return

            # Monitor Updates on /uufi/r/ (sourcing software state exclusively from state/udmi)
            if sub_type == "state" and sub_folder == "udmi" and device_id:
                if "udmi" in payload:
                    self.log_verification(registry_id, device_id, "PROTOCOL VIOLATION: State update wrapped inside 'udmi' root sub-object", level="FAIL")
                    return
                
                system_block = payload.get("system", {})
                software = system_block.get("software", {})
                if software:
                    for subsystem, sub_update in software.items():
                        if isinstance(sub_update, dict):
                            status = sub_update.get("status")
                            if status is None:
                                continue
                                
                            key = (registry_id, device_id, subsystem)
                            prev_status = self.device_states.get(key, "unknown")
                            
                            if status == prev_status:
                                continue
                                
                            self.device_states[key] = status
                            
                            self.log_verification(registry_id, device_id, f"State transition for {registry_id}/{device_id}/{subsystem}: {prev_status} -> {status}", subsystem_id=subsystem)
                            
                            # Check for invalid transitions
                            if prev_status == "quiescent" and status not in ["pending", "quiescent"]:
                                self.log_verification(registry_id, device_id, f"VALIDATION ERROR: {registry_id}/{device_id}/{subsystem} went from quiescent to {status}", level="FAIL", subsystem_id=subsystem)
                            elif prev_status == "pending" and status not in ["success", "failure", "pending"]:
                                self.log_verification(registry_id, device_id, f"VALIDATION ERROR: {registry_id}/{device_id}/{subsystem} went from pending to {status}", level="FAIL", subsystem_id=subsystem)

    def log_verification(self, registry_id, device_id, text, level="PASS", subsystem_id=None):
        log_level = "ERROR" if level == "FAIL" else "INFO"
        print(f"VERIFIER [{log_level}]: {text}", flush=True)
        # UUFI Section 9.4: validation object MUST include message and level
        payload_data = {
            "result": "fail" if level == "FAIL" else "pass",
            "level": log_level,
            "message": text
        }
        if device_id and device_id != "verifier":
            payload_data["device_id"] = device_id
        if subsystem_id:
            payload_data["subsystem_id"] = subsystem_id

        env = create_envelope(
            registry_id=registry_id,
            device_id=device_id,
            sub_type="events",
            sub_folder="validation",
            source=self.conn_spec.source_id
        )
        payload = create_payload("validation", payload_data)
        self.transport.publish(env, payload)

    def run(self):
        self.transport.connect()
        if self.conn_spec.protocol == "mqtt":
            prefix = self.conn_spec.prefix + '/' if self.conn_spec.prefix else ''
            self.transport.subscribe(f"/{prefix}uufi/#", self.on_message)
        else:
            self.transport.subscribe(self.on_message)
        
        self.transport.loop_start()
        
        self.handshake_start_time = time.time()
        last_handshake = 0
        try:
            while True:
                now = time.time()

                # Deduplication cleanup
                to_clear = [dedup_id for dedup_id, ts in self.processed_transactions.items() if now - ts > 300]
                for dedup_id in to_clear:
                    del self.processed_transactions[dedup_id]
                
                if not self.is_active:
                    if self.transport.is_connected:
                        if now - self.handshake_start_time > 60:
                            print("[verifier] CRITICAL: Handshake timeout. Fail-fast.", flush=True)
                            sys.exit(1)
                        if now - last_handshake > 5:
                            self.send_handshake()
                            last_handshake = now
                    else:
                        self.handshake_start_time = now
                
                time.sleep(1)
        except KeyboardInterrupt:
            self.transport.loop_stop()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("pos_conn_spec", nargs="?", help="Connection spec URL")
    parser.add_argument("--conn_spec", help="Connection spec URL")
    args, unknown = parser.parse_known_args()

    conn_str = args.conn_spec or args.pos_conn_spec
    conn_spec = parse_conn_spec(conn_str, differentiator="verifier", is_passive=True)
    sys.stderr.write(f"{conn_spec.format_conn_spec()}\n")
    verifier = Verifier(conn_spec)
    verifier.run()

if __name__ == "__main__":
    main()
