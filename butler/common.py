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
            # /uufi/r/{registryId}/d/{deviceId}/{subType}/{subFolder}
            # /uufi/c/{source}/{subType}/{subFolder}
            
            if len(parts) < 5 or parts[1] != "uufi":
                return

            if parts[2] == "r":
                if len(parts) < 7 or parts[4] != "d":
                    return
                registry_id = parts[3]
                device_id = parts[5]
                sub_type = parts[6]
                sub_folder = parts[7] if len(parts) > 7 else None
            elif parts[2] == "c":
                source = parts[3]
                sub_type = parts[4]
                sub_folder = parts[5] if len(parts) > 5 else None
                device_id = None # Handshake messages might not have a device_id yet
            else:
                return

            # Unwrap payload and merge envelope fields for compatibility
            udmi_payload = data.get("payload", {})
            for k, v in data.items():
                if k != "payload":
                    udmi_payload[k] = v
            
            source = udmi_payload.get("source")
            if source == self.source:
                return

            if self.track_nonces:
                nonce = udmi_payload.get("nonce")
                if nonce and source != self.source:
                    if nonce in self.seen_nonces:
                        return
                    self.seen_nonces.add(nonce)
                    if len(self.seen_nonces) > self.max_seen_nonces:
                        self.seen_nonces.remove(next(iter(self.seen_nonces)))

            # Handshake check
            if sub_type == "config" and sub_folder == "udmi":
                udmi = udmi_payload.get("udmi", {})
                reply = udmi.get("reply", {})
                if reply.get("transaction_id") == self.handshake_transaction_id:
                    self.handshake_complete = True
                    print(f"[{self.source}] Handshake complete!")

            self.on_message(msg.topic, device_id, sub_type, sub_folder, udmi_payload)
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
        nonce = self.generate_nonce()
        
        # Mandatory UDMI fields in payload
        if "timestamp" not in payload_data:
            payload_data["timestamp"] = publish_time
        if "version" not in payload_data:
            payload_data["version"] = "1.5.2"

        is_handshake = (sub_folder == "udmi")

        # Envelope fields
        envelope = {
            "projectId": self.project_id,
            "deviceRegistryId": "" if is_handshake else self.registry_id,
            "deviceId": "" if is_handshake else (device_id or ""),
            "subFolder": sub_folder,
            "subType": sub_type,
            "transactionId": transaction_id or f"UUFI:{self.source}:{nonce}",
            "publishTime": publish_time,
            "source": self.source,
            "nonce": nonce,
            "payload": payload_data
        }
        
        # MQTT Topic Structure according to uufi.md:
        if is_handshake:
            # /uufi/c/{source}/{subType}/{subFolder}
            source_to_use = target_source or self.source
            topic = f"/uufi/c/{source_to_use}/{sub_type}"
        else:
            # /uufi/r/{registryId}/d/{deviceId}/{subType}/{subFolder}
            topic = f"/uufi/r/{self.registry_id}/d/{device_id}/{sub_type}"
            
        if sub_folder:
            topic += f"/{sub_folder}"
        
        self.client.publish(topic, json.dumps(envelope))

    def subscribe_uufi(self, direction="reflect", target_source=None):
        # In the new MQTT scheme, reflect/reply are on the same topics
        # We subscribe to the whole /uufi namespace
        self.client.subscribe("/uufi/#")

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
        self.subscribe_uufi()
        self.publish_uufi(device_id, "state", payload, "udmi", transaction_id=self.handshake_transaction_id)
        print(f"[{self.source}] Started handshake with transaction {self.handshake_transaction_id}")

    def wait_for_handshake(self, timeout=10):
        start_time = time.time()
        while not self.handshake_complete and time.time() - start_time < timeout:
            time.sleep(0.1)
        return self.handshake_complete
