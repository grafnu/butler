import json
import secrets
import datetime
import time
import os
import paho.mqtt.client as mqtt
from urllib.parse import urlparse

def parse_conn_spec(conn_spec):
    """
    Parses a connection string like mqtt://user@host:port/prefix
    Returns (user, host, port, prefix)
    """
    if not conn_spec:
        return None, "localhost", 1883, ""
    
    parsed = urlparse(conn_spec)
    if parsed.scheme != "mqtt":
        # Fallback for simple host or host:port
        if ":" in conn_spec:
            host, port = conn_spec.split(":")
            return None, host, int(port), ""
        return None, conn_spec, 1883, ""

    user = parsed.username
    host = parsed.hostname or "localhost"
    port = parsed.port or 1883
    prefix = parsed.path.strip("/")
    
    return user, host, port, prefix

class ButlerMQTTBase:
    def __init__(self, source, conn_spec=None, track_nonces=True):
        self.source = source
        user, host, port, prefix = parse_conn_spec(conn_spec or os.environ.get("BUTLER_CONN_SPEC"))
        
        self.principal = user or source
        self.host = host
        self.port = port
        self.prefix = prefix
        
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
            
            # Topic patterns:
            # /{prefix}/uufi/p/{principal}/{subType}/{subFolder}
            # /{prefix}/uufi/r/{registryId}/d/{deviceId}/{subType}/{subFolder}
            
            parts = msg.topic.strip("/").split('/')
            
            # Find the 'uufi' index
            try:
                uufi_idx = parts.index("uufi")
            except ValueError:
                return

            if len(parts) < uufi_idx + 3:
                return

            branch = parts[uufi_idx + 1]
            
            if branch == "r":
                if len(parts) < uufi_idx + 5 or parts[uufi_idx + 3] != "d":
                    return
                registry_id = parts[uufi_idx + 2]
                device_id = parts[uufi_idx + 4]
                sub_type = parts[uufi_idx + 5]
                sub_folder = parts[uufi_idx + 6] if len(parts) > uufi_idx + 6 else None
            elif branch == "p":
                principal = parts[uufi_idx + 2]
                sub_type = parts[uufi_idx + 3]
                sub_folder = parts[uufi_idx + 4] if len(parts) > uufi_idx + 4 else None
                device_id = None
            else:
                return

            # Unwrap payload and merge envelope fields for compatibility
            udmi_payload = data.get("payload", {})
            for k, v in data.items():
                if k != "payload":
                    udmi_payload[k] = v
            
            msg_source = udmi_payload.get("source")
            if msg_source == self.source:
                return

            if self.track_nonces:
                nonce = udmi_payload.get("nonce")
                if nonce:
                    if nonce in self.seen_nonces:
                        return
                    self.seen_nonces.add(nonce)
                    if len(self.seen_nonces) > self.max_seen_nonces:
                        self.seen_nonces.remove(next(iter(self.seen_nonces)))

            # Handshake check
            if sub_type == "config" and sub_folder == "udmi":
                udmi = udmi_payload.get("udmi", {})
                reply = udmi.get("reply")
                if reply and reply.get("transaction_id") == self.handshake_transaction_id:
                    self.handshake_complete = True
                    print(f"[{self.source}] Handshake complete!")

            self.on_message(msg.topic, device_id, sub_type, sub_folder, udmi_payload)
        except Exception as e:
            # If not valid JSON, we might still want to see it if we are an observer
            # but for the base class we just ignore it unless specifically handled
            pass

    def on_connect(self):
        pass

    def on_message(self, topic, device_id, sub_type, sub_folder, data):
        pass

    def connect(self):
        print(f"[{self.source}] Connecting to MQTT broker at {self.host}:{self.port}...")
        print(f"[{self.source}] Project ID: {self.project_id}, Registry ID: {self.registry_id}")
        if self.prefix:
            print(f"[{self.source}] Topic Prefix: {self.prefix}")
        self.client.connect(self.host, self.port, 60)

    def loop_start(self):
        self.client.loop_start()

    def loop_stop(self):
        self.client.loop_stop()

    def loop_forever(self):
        self.client.loop_forever()

    def generate_nonce(self):
        return secrets.token_hex(4) # 8-digit hex

    def publish_uufi(self, device_id, sub_type, payload_data, sub_folder=None, direction="reflect", target_principal=None, transaction_id=None):
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
        
        # Topic Structure
        topic_parts = []
        if self.prefix:
            topic_parts.append(self.prefix)
        topic_parts.append("uufi")
        
        if is_handshake:
            topic_parts.append("p")
            topic_parts.append(target_principal or self.principal)
        else:
            topic_parts.append("r")
            topic_parts.append(self.registry_id)
            topic_parts.append("d")
            topic_parts.append(device_id)
        
        topic_parts.append(sub_type)
        if sub_folder:
            topic_parts.append(sub_folder)
        
        topic = "/" + "/".join(topic_parts)
        self.client.publish(topic, json.dumps(envelope))

    def subscribe_uufi(self):
        # Subscribe to all traffic under the prefix
        topic = "/#"
        if self.prefix:
            topic = f"/{self.prefix}/#"
        else:
            topic = "/uufi/#"
        self.client.subscribe(topic)

    def start_handshake(self, device_id=None):
        self.handshake_transaction_id = f"UUFI:{self.source}:{self.generate_nonce()}"
        payload = {
            "udmi": {
                "setup": {
                    "functions_ver": 9,
                    "transaction_id": self.handshake_transaction_id,
                    "msg_source": self.source,
                    "user": self.principal
                }
            }
        }
        self.subscribe_uufi()
        self.publish_uufi(None, "state", payload, "udmi", transaction_id=self.handshake_transaction_id)
        print(f"[{self.source}] Started handshake with transaction {self.handshake_transaction_id}")

    def wait_for_handshake(self, timeout=10):
        start_time = time.time()
        while not self.handshake_complete and time.time() - start_time < timeout:
            time.sleep(0.1)
        return self.handshake_complete
