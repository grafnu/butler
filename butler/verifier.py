import paho.mqtt.client as mqtt
import json
import time
import sys
import argparse
import re
from butler.messaging import parse_uufi_message, create_uufi_message
from butler.conn_spec import parse_conn_spec

class Verifier:
    def __init__(self, conn_spec):
        self.conn_spec = conn_spec
        self.client = mqtt.Client()
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        self.device_states = {} # device_id: last_state
        self.handshakes = {} # principal: {tid, active}
        self.timestamp_regex = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$')
        self.registry_id = "butler-registry"

    def get_topic(self, base, suffix=None):
        parts = ["uufi"]
        if self.conn_spec.prefix:
            parts.append(self.conn_spec.prefix)
        parts.append(base)
        if suffix:
            parts.append(suffix)
        return "/" + "/".join(parts)

    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            topic = "/uufi/#"
            if self.conn_spec.prefix:
                topic = f"/uufi/{self.conn_spec.prefix}/#"
            client.subscribe(topic)
        else:
            print(f"Verifier failed to connect: {rc}", flush=True)

    def on_message(self, client, userdata, msg):
        message, envelope = parse_uufi_message(msg.payload)
        if not message:
            return

        # Mandatory field validation
        if "timestamp" not in message or "version" not in message:
            self.log_verification("Missing mandatory UDMI fields (timestamp or version)", level="FAIL")
            return
        
        timestamp = message.get("timestamp")
        if not self.timestamp_regex.match(timestamp):
            self.log_verification(f"Invalid timestamp format: {timestamp}", level="FAIL")
            return

        topic = msg.topic
        parts = topic.split('/')
        offset = 1 if self.conn_spec.prefix else 0
        
        if len(parts) < 5 + offset:
            return

        # Monitor Handshake
        if parts[2+offset] == "p":
            principal = parts[3+offset]
            sub_type = parts[4+offset]
            sub_folder = parts[5+offset]
            
            if sub_type == "state" and sub_folder == "udmi":
                udmi = message.get("udmi", {})
                setup = udmi.get("setup", {})
                tid = setup.get("transaction_id")
                self.handshakes[principal] = {"tid": tid, "active": False}
                self.log_verification(f"Handshake started for {principal} (tid: {tid})")
            
            elif sub_type == "config" and sub_folder == "udmi":
                udmi = message.get("udmi", {})
                reply = udmi.get("reply", {})
                tid = reply.get("transaction_id")
                if principal in self.handshakes and self.handshakes[principal]["tid"] == tid:
                    self.handshakes[principal]["active"] = True
                    self.log_verification(f"Handshake complete for {principal} (tid: {tid})")

        # Monitor Updates
        elif parts[3+offset] == "r":
            if len(parts) < 8 + offset: return
            device_id = parts[5+offset]
            sub_type = parts[6+offset]
            sub_folder = parts[7+offset]

            if sub_folder == "update":
                update = message.get("update", {})
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
        payload = {
            "result": level,
            "message": text,
            "timestamp": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        }
        msg = create_uufi_message(
            registry_id=self.registry_id,
            device_id="butler",
            sub_type="events",
            sub_folder="verify",
            payload=payload,
            source="verifier"
        )
        topic = self.get_topic(f"r/{self.registry_id}/d/butler/events/verify")
        self.client.publish(topic, json.dumps(msg))

    def run(self):
        host = self.conn_spec.host
        port = self.conn_spec.port or 1883
        print(f"Verifier connecting to MQTT broker at {host}:{port}", flush=True)
        if self.conn_spec.username:
            self.client.username_pw_set(self.conn_spec.username)
        self.client.connect(host, port, 60)
        self.client.loop_forever()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("conn_spec", help="Connection spec URL")
    args = parser.parse_args()

    conn_spec = parse_conn_spec(args.conn_spec)
    verifier = Verifier(conn_spec)
    print("Starting Verifier Watcher...")
    verifier.run()

if __name__ == "__main__":
    main()
