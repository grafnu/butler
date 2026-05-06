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
        self.device_states = {} # device_id: last_state
        self.handshakes = {} # principal: {tid, active}
        self.timestamp_regex = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$')
        self.registry_id = "butler-registry"

    def on_message(self, env, payload, topic, raw=None):
        if not payload: return
        # Mandatory field validation
        if "timestamp" not in payload or "version" not in payload:
            self.log_verification("Missing mandatory UDMI fields (timestamp or version)", level="FAIL")
            return
        
        timestamp = payload.get("timestamp")
        if not self.timestamp_regex.match(timestamp):
            self.log_verification(f"Invalid timestamp format: {timestamp}", level="FAIL")
            return

        sub_type = env.get("subType")
        sub_folder = env.get("subFolder")
        device_id = env.get("deviceId")
        principal = env.get("principal")

        # Monitor Handshake
        if sub_folder == "udmi" and not device_id:
            if sub_type == "state":
                udmi = payload.get("udmi", payload)
                setup = udmi.get("setup", {})
                tid = setup.get("transaction_id")
                self.handshakes[principal] = {"tid": tid, "active": False}
                self.log_verification(f"Handshake started for {principal} (tid: {tid})")
            
            elif sub_type == "config":
                udmi = payload.get("udmi", payload)
                reply = udmi.get("reply", {})
                tid = reply.get("transaction_id")
                if principal in self.handshakes and self.handshakes[principal]["tid"] == tid:
                    self.handshakes[principal]["active"] = True
                    self.log_verification(f"Handshake complete for {principal} (tid: {tid})")

        # Monitor Updates
        elif sub_type == "state" and sub_folder == "update":
            update = payload.get("update", {})
            state = update.get("state")
            current_version = update.get("current_version")
            
            prev_state = self.device_states.get(device_id)
            self.device_states[device_id] = state
            
            self.log_verification(f"Device {device_id} state transition: {prev_state} -> {state} ({current_version})")
            
            # Check for invalid transitions
            if prev_state == "quiescent" and state not in ["pending", "quiescent"]:
                self.log_verification(f"INVALID TRANSITION: {device_id} went from quiescent to {state}", level="FAIL")
            elif prev_state == "pending" and state not in ["success", "failure", "pending"]:
                self.log_verification(f"INVALID TRANSITION: {device_id} went from pending to {state}", level="FAIL")

    def log_verification(self, text, level="PASS"):
        print(f"[verifier] {level}: {text}", flush=True)
        payload_data = {
            "result": level,
            "message": text,
            "timestamp": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        }
        env = create_envelope(
            registry_id=self.registry_id,
            device_id="butler",
            sub_type="events",
            sub_folder="verify",
            source="verifier"
        )
        payload = create_payload("verify", payload_data)
        self.transport.publish(env, payload)

    def run(self):
        self.transport.connect()
        if self.conn_spec.protocol == "mqtt":
            self.transport.subscribe("/uufi/#" if not self.conn_spec.prefix else f"/uufi/{self.conn_spec.prefix}/#", self.on_message)
        else:
            self.transport.subscribe(self.on_message)
        
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
