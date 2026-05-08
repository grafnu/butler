import paho.mqtt.client as mqtt
import json
import time
import sys
import argparse
import re
import datetime
from butler.messaging import create_payload, create_envelope
from butler.conn_spec import parse_conn_spec
from butler.transport import get_transport

class Verifier:
    def __init__(self, conn_spec):
        self.conn_spec = conn_spec
        self.transport = get_transport(conn_spec)
        self.device_states = {} # (registry_id, device_id, subsystem): last_state
        self.handshakes = {} # principal: {tid, active}
        # Strict minimal precision format: 2026-05-01T22:32:17Z
        self.strict_ts_regex = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$')
        # Graceful format (permissive RFC 3339)
        self.graceful_ts_regex = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$')
        self.default_registry_id = "default"

    def on_message(self, env, payload, topic, raw=None):
        if not payload: return
        
        sub_type = env.get("subType")
        sub_folder = env.get("subFolder")
        device_id = env.get("deviceId")
        registry_id = env.get("deviceRegistryId") or self.default_registry_id
        principal = env.get("principal")
        source = env.get("source")

        # Mandatory field validation
        if "timestamp" not in payload or "version" not in payload:
            self.log_verification(registry_id, device_id or "_validator", "Missing mandatory UDMI fields (timestamp or version)", level="FAIL")
            return
        
        timestamp = payload.get("timestamp")
        # Strict for butler, graceful for everyone else
        if source == "butler":
            if not self.strict_ts_regex.match(timestamp):
                self.log_verification(registry_id, device_id or "_validator", f"Butler emitted non-strict timestamp: {timestamp}", level="FAIL")
                return
        else:
            if not self.graceful_ts_regex.match(timestamp):
                self.log_verification(registry_id, device_id or "_validator", f"Invalid timestamp format from {source}: {timestamp}", level="FAIL")
                return

        # Monitor Handshake on /uufi/c/
        if sub_folder == "udmi" and not device_id:
            if sub_type == "state":
                udmi = payload.get("udmi", payload)
                setup = udmi.get("setup", {})
                tid = setup.get("transaction_id")
                if principal:
                    self.handshakes[principal] = {"tid": tid, "active": False}
                    self.log_verification(registry_id, "_validator", f"Handshake started for {principal} (tid: {tid})")

                # Send Handshake Reply
                reply_payload_data = {
                    "setup": {
                        "functions_min": 9,
                        "functions_max": 9,
                        "udmi_version": "1.5.2"
                    },
                    "reply": setup
                }
                reply_env = create_envelope(
                    sub_type="config",
                    sub_folder="udmi",
                    transaction_id=tid,
                    source="verifier"
                )
                if principal:
                    reply_env["principal"] = principal
                self.transport.publish(reply_env, create_payload("udmi", reply_payload_data))

            elif sub_type == "config":
                udmi = payload.get("udmi", payload)
                reply = udmi.get("reply", {})
                tid = reply.get("transaction_id")
                if principal in self.handshakes and self.handshakes[principal]["tid"] == tid:
                    self.handshakes[principal]["active"] = True
                    self.log_verification(registry_id, "_validator", f"Handshake complete for {principal} (tid: {tid})")

        # Monitor Updates on /uufi/r/
        elif sub_type == "state" and sub_folder == "update" and device_id:
            update = payload.get("update", {})
            subsystem = update.get("subsystem", "main")
            status = update.get("status")
            current_version = update.get("current_version")
            
            key = (registry_id, device_id, subsystem)
            prev_status = self.device_states.get(key)
            self.device_states[key] = status
            
            self.log_verification(registry_id, device_id, f"Device {registry_id}/{device_id}/{subsystem} status transition: {prev_status} -> {status} ({current_version})")
            
            # Check for invalid transitions
            if prev_status == "quiescent" and status not in ["pending", "quiescent"]:
                self.log_verification(registry_id, device_id, f"INVALID TRANSITION: {registry_id}/{device_id}/{subsystem} went from quiescent to {status}", level="FAIL")
            elif prev_status == "pending" and status not in ["success", "failure", "pending"]:
                self.log_verification(registry_id, device_id, f"INVALID TRANSITION: {registry_id}/{device_id}/{subsystem} went from pending to {status}", level="FAIL")

    def log_verification(self, registry_id, device_id, text, level="PASS"):
        print(f"[verifier] {level}: {text}", flush=True)
        payload_data = {
            "result": level,
            "message": text,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        }
        if level == "FAIL":
            payload_data["category"] = "validation.error"
        env = create_envelope(
            registry_id=registry_id,
            device_id=device_id,
            sub_type="events",
            sub_folder="validation",
            source="verifier"
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
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            self.transport.loop_stop()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("conn_spec", nargs="?", help="Connection spec URL")
    args = parser.parse_args()

    conn_spec = parse_conn_spec(args.conn_spec, differentiator="verifier")
    verifier = Verifier(conn_spec)
    print(f"Starting Verifier Watcher with {conn_spec}...")
    verifier.run()

if __name__ == "__main__":
    main()
