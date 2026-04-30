import json
import secrets
import datetime
import time
import os
import paho.mqtt.client as mqtt

class ButlerMQTTBase:
    def __init__(self, source, host=None, port=None, track_nonces=True):
        self.source = source
        self.host = host or os.environ.get("MQTT_HOST", "localhost")
        self.port = int(port or os.environ.get("MQTT_PORT", 1883))
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
            print(f"[{self.source}] Successfully connected to MQTT broker at {self.host}:{self.port}")
            self.on_connect()
        else:
            print(f"Connection failed with code {rc}")

    def _on_message(self, client, userdata, msg):
        try:
            data = json.loads(msg.payload.decode())
            
            parts = msg.topic.split('/')
            if parts[0] != "udmi":
                return

            direction = parts[1]
            # udmi/{direction}[/{source}]/{projectId}/{registryId}/{deviceId}/{subType}/{subFolder}
            
            # Find offset based on whether source is present
            # If direction is reply, it might have a source: udmi/reply/source/...
            # If direction is reflect, it doesn't usually have a source in the topic path per uufi.md 
            # but butler uses it. Let's look at how publish_uufi does it.
            
            if direction == "reply" and parts[2] != self.project_id:
                source = parts[2]
                offset = 3
            else:
                source = data.get("source")
                offset = 2

            project_id = parts[offset]
            registry_id = parts[offset+1]
            device_id = parts[offset+2]
            sub_type = parts[offset+3]
            sub_folder = parts[offset+4] if len(parts) > offset+4 else None

            if self.track_nonces:
                nonce = data.get("nonce")
                if nonce and source != self.source:
                    if nonce in self.seen_nonces:
                        return
                    self.seen_nonces.add(nonce)
                    if len(self.seen_nonces) > self.max_seen_nonces:
                        # self.seen_nonces is a set, so we can't pop(0). 
                        # Butler used self.seen_nonces.pop() which is fine for a set.
                        self.seen_nonces.remove(next(iter(self.seen_nonces)))

            # Handshake check
            if sub_type == "config" and sub_folder == "udmi":
                udmi = data.get("udmi", {})
                reply = udmi.get("reply", {})
                if reply.get("transaction_id") == self.handshake_transaction_id:
                    self.handshake_complete = True
                    print(f"[{self.source}] Handshake complete!")

            self.on_message(msg.topic, device_id, sub_type, sub_folder, data)
        except Exception as e:
            if self.track_nonces:
                print(f"[{self.source}] Error decoding message on {msg.topic}: {e}")

    def on_connect(self):
        pass

    def on_message(self, topic, device_id, sub_type, sub_folder, data):
        pass

    def connect(self):
        print(f"[{self.source}] Connecting to MQTT broker at {self.host}:{self.port}...")
        print(f"[{self.source}] Project ID: {self.project_id}, Registry ID: {self.registry_id}")
        self.client.connect(self.host, self.port, 60)

    def loop_start(self):
        self.client.loop_start()

    def loop_stop(self):
        self.client.loop_stop()

    def loop_forever(self):
        self.client.loop_forever()

    def generate_nonce(self):
        return secrets.token_hex(4)

    def publish_uufi(self, device_id, sub_type, payload_data, sub_folder=None, direction="reflect", target_source=None, transaction_id=None):
        publish_time = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        data = {
            "uufi_version": "1.5.2",
            "publish_time": publish_time,
            "source": self.source,
            "nonce": self.generate_nonce()
        }
        if transaction_id:
            data["transaction_id"] = transaction_id
        
        data.update(payload_data)
        
        # Canonical MQTT topic mapping for UUFI
        # udmi/{direction}[/{source}]/{projectId}/{registryId}/{deviceId}/{subType}/{subFolder}
        topic = f"udmi/{direction}"
        if direction == "reply" and target_source:
            topic += f"/{target_source}"
        
        topic += f"/{self.project_id}/{self.registry_id}/{device_id}/{sub_type}"
        if sub_folder:
            topic += f"/{sub_folder}"
        
        self.client.publish(topic, json.dumps(data))

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
        self.publish_uufi(device_id, "state", payload, "udmi", transaction_id=self.handshake_transaction_id)
        print(f"[{self.source}] Started handshake with transaction {self.handshake_transaction_id}")

    def wait_for_handshake(self, timeout=10):
        start_time = time.time()
        while not self.handshake_complete and time.time() - start_time < timeout:
            time.sleep(0.1)
        return self.handshake_complete
