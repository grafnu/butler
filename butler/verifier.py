import paho.mqtt.client as mqtt
import json
import time
import sys
import argparse
import re
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
        self.default_registry_id = "butler-registry"

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
            self.log_verification(registry_id, "Missing mandatory UDMI fields (timestamp or version)", level="FAIL")
            return
        
        timestamp = payload.get("timestamp")
        # Strict for butler, graceful for everyone else
        if source == "butler":
            if not self.strict_ts_regex.match(timestamp):
                self.log_verification(registry_id, f"Butler emitted non-strict timestamp: {timestamp}", level="FAIL")
                return
        else:
            if not self.graceful_ts_regex.match(timestamp):
                self.log_verification(registry_id, f"Invalid timestamp format from {source}: {timestamp}", level="FAIL")
                return

        # Monitor Handshake on /uufi/p/
        if sub_folder == "udmi" and not device_id:
            if sub_type == "state":
                udmi = payload.get("udmi", payload)
                setup = udmi.get("setup", {})
                tid = setup.get("transaction_id")
                self.handshakes[principal] = {"tid": tid, "active": False}
                self.log_verification(registry_id, f"Handshake started for {principal} (tid: {tid})")
            
            elif sub_type == "config":
                udmi = payload.get("udmi", payload)
                reply = udmi.get("reply", {})
                tid = reply.get("transaction_id")
                if principal in self.handshakes and self.handshakes[principal]["tid"] == tid:
                    self.handshakes[principal]["active"] = True
                    self.log_verification(registry_id, f"Handshake complete for {principal} (tid: {tid})")

        # Monitor Updates on /uufi/r/
        elif sub_type == "state" and sub_folder == "update" and device_id:
            update = payload.get("update", {})
            subsystem = update.get("subsystem", "main")
            state = update.get("state")
            current_version = update.get("current_version")
            
            key = (registry_id, device_id, subsystem)
            prev_state = self.device_states.get(key)
            self.device_states[key] = state
            
            self.log_verification(registry_id, f"Device {registry_id}/{device_id}/{subsystem} state transition: {prev_state} -> {state} ({current_version})")
            
            # Check for invalid transitions
            if prev_state == "quiescent" and state not in ["pending", "quiescent"]:
                self.log_verification(registry_id, f"INVALID TRANSITION: {registry_id}/{device_id}/{subsystem} went from quiescent to {state}", level="FAIL")
            elif prev_state == "pending" and state not in ["success", "failure", "pending"]:
                self.log_verification(registry_id, f"INVALID TRANSITION: {registry_id}/{device_id}/{subsystem} went from pending to {state}", level="FAIL")

    def log_verification(self, registry_id, text, level="PASS"):
        print(f"[verifier] {level}: {text}", flush=True)
        payload_data = {
            "result": level,
            "message": text,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        }
        env = create_envelope(
            registry_id=registry_id,
            device_id="_validator",
            sub_type="events",
            sub_folder="validation",
            source="verifier"
        )
        payload = create_payload("validation", payload_data)
        self.transport.publish(env, payload)

    def run(self):
        self.transport.connect()
        if self.conn_spec.protocol == "mqtt":
            self.transport.subscribe("/uufi/#" if not self.conn_spec.prefix else f"/uufi/{self.conn_spec.prefix}/#", self.on_message)
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
