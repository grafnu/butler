import paho.mqtt.client as mqtt
import json
import time
import sys
import os
import hashlib
import argparse
from butler.messaging import create_uufi_message, parse_uufi_message
from butler.model_repo import ModelRepository
from butler.conn_spec import parse_conn_spec

class MockDevice:
    def __init__(self, conn_spec, device_id, fail_mode=False):
        self.conn_spec = conn_spec
        self.device_id = device_id
        self.fail_mode = fail_mode
        self.current_version = "0.0.0" 
        self.state = "quiescent"
        self.client = mqtt.Client()
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        self.subsystem = "main"
        self.seen_nonces = set()
        self.model_repo = ModelRepository()
        self.registry_id = "butler-registry"
        
        # Initialize state from model repo if available
        dev_info = self.model_repo.get_device_state(self.device_id, self.subsystem)
        if dev_info:
            self.current_version = dev_info.get("current_version", "0.0.0")
            print(f"[mockit] Initialized {self.device_id} current_version to {self.current_version}", flush=True)
        else:
            self.current_version = "0.0.0"

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
            client.subscribe(self.get_topic(f"r/{self.registry_id}/d/{self.device_id}/config/update"))
            client.subscribe(self.get_topic("p/+/state/udmi"))
            client.subscribe(self.get_topic(f"r/{self.registry_id}/d/{self.registry_id}/query/cloud"))
            client.subscribe(self.get_topic(f"r/{self.registry_id}/d/{self.registry_id}/model/cloud"))
            self.report_status()
        else:
            print(f"Device {self.device_id} failed to connect: {rc}", flush=True)

    def on_message(self, client, userdata, msg):
        message, envelope = parse_uufi_message(msg.payload)
        if not message:
            return

        topic = msg.topic
        
        # Check for handshake state from clients
        # Topic: /uufi/[prefix/]p/{principal}/state/udmi
        parts = topic.split('/')
        offset = 1 if self.conn_spec.prefix else 0
        if len(parts) >= 6 + offset and parts[1] == "uufi" and parts[2+offset] == "p":
            principal = parts[3+offset]
            sub_type = parts[4+offset]
            sub_folder = parts[5+offset]
            if sub_type == "state" and sub_folder == "udmi":
                self.handle_handshake(principal, message)
            return

        if len(parts) < 8 + offset:
            return
        
        device_id = parts[5+offset]
        sub_type = parts[6+offset]
        sub_folder = parts[7+offset]

        if device_id == self.registry_id and sub_folder == "cloud":
            self.handle_cloud_op(sub_type, message)
            return

        if device_id == self.device_id and sub_type == "config" and sub_folder == "update":
            nonce = message.get("nonce")
            if nonce in self.seen_nonces:
                return
            self.seen_nonces.add(nonce)
            if len(self.seen_nonces) > 1000:
                self.seen_nonces.clear()

            update = message.get("update", {})
            url = update.get("url")
            sha256 = update.get("sha256")
            version = update.get("version")

            if not version:
                return

            print(f"[mockit] Device {self.device_id} received update to {version}", flush=True)
            self.state = "pending"
            self.report_status()

            if self.fail_mode:
                print(f"[mockit] FAILURE MODE: Not progressing from pending.", flush=True)
                return

            time.sleep(1)
            try:
                if not os.path.exists(url):
                    print(f"[mockit] Blob not found: {url}", flush=True)
                    self.state = "failure"
                    self.report_status()
                    return

                with open(url, "rb") as f:
                    actual_hash = hashlib.sha256(f.read()).hexdigest()

                if actual_hash == sha256:
                    time.sleep(1)
                    self.current_version = version
                    self.state = "success"
                    self.report_status()
                    self.state = "quiescent"
                else:
                    print(f"[mockit] Hash mismatch for {url}: expected {sha256}, got {actual_hash}", flush=True)
                    self.state = "failure"
                    self.report_status()
            except Exception as e:
                print(f"[mockit] Error applying update: {e}", flush=True)
                self.state = "failure"
                self.report_status()

    def handle_handshake(self, principal, message):
        udmi = message.get("udmi", {})
        setup = udmi.get("setup", {})
        transaction_id = setup.get("transaction_id")

        print(f"[mockit] Received UUFI handshake state from {principal} (tid: {transaction_id})", flush=True)

        reply_payload = {
            "udmi": {
                "setup": {
                    "functions_min": 9,
                    "functions_max": 9,
                    "udmi_version": "1.5.2"
                },
                "reply": setup
            }
        }

        msg = create_uufi_message(
            registry_id=self.registry_id,
            device_id=principal,
            sub_type="config",
            sub_folder="udmi",
            payload=reply_payload["udmi"],
            transaction_id=transaction_id,
            source="mockit"
        )
        topic = self.get_topic(f"p/{principal}/config/udmi")
        self.client.publish(topic, json.dumps(msg))
        self.push_model()

    def handle_cloud_op(self, sub_type, message):
        cloud = message.get("cloud", {})
        operation = cloud.get("operation")

        if sub_type == "query" and operation == "READ":
            self.push_model()
        elif sub_type == "model" and operation == "UPDATE":
            dev_id = cloud.get("device_id")
            subsystem = cloud.get("subsystem")
            target = cloud.get("target_version")
            current = cloud.get("current_version")

            if target is not None:
                self.model_repo.set_target_version(dev_id, subsystem, target)
            if current is not None:
                self.model_repo.update_current_version(dev_id, subsystem, current)
            self.push_model()

    def push_model(self):
        self.model_repo.reload()
        model_payload = self.model_repo.data # This contains {"devices": {...}}
        msg = create_uufi_message(
            registry_id=self.registry_id,
            device_id=self.registry_id,
            sub_type="config",
            sub_folder="cloud",
            payload=model_payload,
            source="mockit"
        )
        topic = self.get_topic(f"r/{self.registry_id}/d/{self.registry_id}/config/cloud")
        self.client.publish(topic, json.dumps(msg))

    def report_status(self):
        update_payload = {
            "current_version": self.current_version,
            "state": self.state,
            "subsystem": self.subsystem
        }
        msg = create_uufi_message(
            registry_id=self.registry_id,
            device_id=self.device_id,
            sub_type="state",
            sub_folder="update",
            payload=update_payload,
            source="mockit"
        )
        topic = self.get_topic(f"r/{self.registry_id}/d/{self.device_id}/state/update")
        self.client.publish(topic, json.dumps(msg))

    def run(self):
        host = self.conn_spec.host
        port = self.conn_spec.port or 1883
        print(f"Mocket connecting to MQTT broker at {host}:{port}", flush=True)
        if self.conn_spec.username:
            self.client.username_pw_set(self.conn_spec.username)
        self.client.connect(host, port, 60)
        self.client.loop_start()
        
        last_push = 0
        try:
            while True:
                now = time.time()
                if now - last_push > 5:
                    self.push_model()
                    last_push = now
                self.report_status()
                time.sleep(10)
        except KeyboardInterrupt:
            self.client.loop_stop()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("conn_spec", help="Connection spec URL")
    parser.add_argument("device_id")
    parser.add_argument("-f", action="store_true", help="Enable failure mode")
    args = parser.parse_args()

    conn_spec = parse_conn_spec(args.conn_spec)
    device = MockDevice(conn_spec, args.device_id, fail_mode=args.f)
    print(f"Starting mock device {args.device_id}...")
    device.run()

if __name__ == "__main__":
    main()
