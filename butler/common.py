import json
import secrets
import datetime
import time
import os
import paho.mqtt.client as mqtt

class ButlerMQTTBase:
    def __init__(self, source, host="localhost", port=1883, track_nonces=True):
        self.source = source
        self.host = host
        self.port = port
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.handshake_complete = False
        self.handshake_transaction_id = None
        self.project_id = os.environ.get("BUTLER_PROJECT_ID", "vibrant")
        self.registry_id = os.environ.get("BUTLER_REGISTRY_ID", "controller")
        self.track_nonces = track_nonces
        self.seen_nonces = set()
        self.max_seen_nonces = 1000

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            print(f"[{self.source}] Connected to MQTT broker at {self.host}:{self.port}")
            self.on_connect()
        else:
            print(f"Connection failed with code {rc}")

    def _on_message(self, client, userdata, msg):
        try:
            data = json.loads(msg.payload.decode())
            if self.track_nonces:
                nonce = data.get("nonce")
                if nonce:
                    if nonce in self.seen_nonces:
                        # Duplicate message, ignore
                        return
                    self.seen_nonces.add(nonce)
                    if len(self.seen_nonces) > self.max_seen_nonces:
                        # Simple cleanup
                        self.seen_nonces.pop()

            parts = msg.topic.split('/')
            
            if parts[0] == "udmi":
                direction = parts[1]
                if direction == "reflect":
                    offset = 2
                else: # reply
                    if parts[2] == self.project_id:
                        offset = 2
                    else:
                        offset = 3
                
                device_id = parts[offset+2] if len(parts) > offset+2 else None
                sub_type = parts[offset+3] if len(parts) > offset+3 else None
                sub_folder = parts[offset+4] if len(parts) > offset+4 else None
            else:
                device_id = parts[1] if len(parts) > 1 else None
                sub_type = parts[2] if len(parts) > 2 else None
                sub_folder = parts[3] if len(parts) > 3 else None

            # Check for handshake config (System reply to Client)
            if sub_type == "config" and sub_folder == "udmi":
                payload = data.get("payload", {})
                udmi = payload.get("udmi", {})
                reply = udmi.get("reply", {})
                if reply.get("transaction_id") == self.handshake_transaction_id:
                    self.handshake_complete = True
                    print(f"[{self.source}] Handshake complete!")

            self.on_message(msg.topic, device_id, sub_type, sub_folder, data)
        except Exception as e:
            print(f"Error decoding message on {msg.topic}: {e}")

    def on_connect(self):
        pass

    def on_message(self, topic, device_id, sub_type, sub_folder, data):
        pass

    def connect(self):
        self.client.connect(self.host, self.port, 60)

    def loop_start(self):
        self.client.loop_start()

    def loop_stop(self):
        self.client.loop_stop()

    def loop_forever(self):
        self.client.loop_forever()

    def generate_nonce(self):
        return secrets.token_hex(4)

    def publish_uufi(self, device_id, sub_type, payload, sub_folder=None, direction="reflect", target_source=None):
        envelope = {
            "version": "1.5.2",
            "timestamp": datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            "source": self.source,
            "nonce": self.generate_nonce(),
            "projectId": self.project_id,
            "deviceRegistryId": self.registry_id,
            "deviceId": device_id,
            "subType": sub_type,
            "subFolder": sub_folder,
            "payload": payload
        }
        
        topic = f"udmi/{direction}"
        if direction == "reply" and target_source:
            topic += f"/{target_source}"
        
        topic += f"/{self.project_id}/{self.registry_id}/{device_id}/{sub_type}"
        if sub_folder:
            topic += f"/{sub_folder}"
        
        self.client.publish(topic, json.dumps(envelope))

    def subscribe_uufi(self, direction="reflect", target_source=None):
        topic = f"udmi/{direction}"
        if target_source:
            topic += f"/{target_source}"
        topic += "/#"
        self.client.subscribe(topic)

    def start_handshake(self, device_id=None):
        device_id = device_id or self.registry_id
        self.handshake_transaction_id = f"UUFI:{self.source}:{self.generate_nonce()}"
        payload = {
            "udmi": {
                "setup": {
                    "functions_ver": 9,
                    "transaction_id": self.handshake_transaction_id,
                    "msg_source": self.source,
                    "user": self.source
                }
            }
        }
        self.subscribe_uufi(direction="reply", target_source=self.source)
        self.publish_uufi(device_id, "state", payload, "udmi")
        print(f"[{self.source}] Started handshake with transaction {self.handshake_transaction_id}")

    def wait_for_handshake(self, timeout=10):
        start_time = time.time()
        while not self.handshake_complete and time.time() - start_time < timeout:
            time.sleep(0.1)
        return self.handshake_complete
