import json
import secrets
import datetime
import time
import os
import paho.mqtt.client as mqtt
from urllib.parse import urlparse

def get_default_conn_spec():
    """
    Returns the default connection specification.
    Prefers BUTLER_CONN_SPEC env var, otherwise mqtt://<branch>@localhost/
    """
    spec = os.environ.get("BUTLER_CONN_SPEC")
    if spec:
        return spec
    try:
        import subprocess
        branch = subprocess.check_output(["git", "rev-parse", "--abbrev-ref", "HEAD"], stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        branch = "unknown"
    return f"mqtt://{branch}@localhost/"

def parse_conn_spec(conn_spec):
    """
    Parses a connection string like:
    mqtt://user@host:port/prefix
    pubsub://user@project/topic
    Returns (scheme, user, host, port_or_topic, prefix)
    """
    if not conn_spec:
        return "mqtt", None, "localhost", 1883, ""
    
    parsed = urlparse(conn_spec)
    scheme = parsed.scheme or "mqtt"
    user = parsed.username
    host = parsed.hostname or "localhost"
    
    if scheme == "pubsub":
        topic = parsed.path.strip("/") or "udmi_uufi"
        return scheme, user, host, topic, ""
    
    # MQTT
    port = parsed.port or 1883
    prefix = parsed.path.strip("/")
    return scheme, user, host, port, prefix

class ButlerBusBase:
    def __init__(self, source, conn_spec, track_nonces=True):
        self.source = source
        self.conn_spec = conn_spec
        scheme, user, host, port_or_topic, prefix = parse_conn_spec(self.conn_spec)
        
        self.scheme = scheme
        self.principal = user or source
        self.host = host # project_id for pubsub
        self.port_or_topic = port_or_topic
        self.prefix = prefix
        
        self.handshake_complete = False
        self.handshake_transaction_id = None
        self.project_id = os.environ.get("BUTLER_PROJECT_ID", "vibrant")
        self.registry_id = os.environ.get("BUTLER_REGISTRY_ID", "controller")
        self.track_nonces = track_nonces
        self.seen_nonces = set()
        self.max_seen_nonces = 1000
        self.filter_principal = source not in ["butler", "verifier", "observe"]

    def generate_nonce(self):
        return secrets.token_hex(4) # 8-digit hex

    def _handle_received_message(self, topic, device_id, sub_type, sub_folder, udmi_payload):
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

        # Handshake check (look inside 'udmi' subfolder)
        if sub_type == "config" and sub_folder == "udmi":
            udmi_block = udmi_payload.get("udmi", {})
            reply = udmi_block.get("reply")
            if reply and reply.get("transaction_id") == self.handshake_transaction_id:
                self.handshake_complete = True
                print(f"[{self.source}] Handshake complete!")

        self.on_message(topic, device_id, sub_type, sub_folder, udmi_payload)

    def on_connect(self):
        pass

    def on_message(self, topic, device_id, sub_type, sub_folder, data):
        pass

    def connect(self):
        pass

    def loop_start(self):
        pass

    def loop_forever(self):
        pass

    def publish_uufi(self, device_id, sub_type, payload_data, sub_folder=None, direction="reflect", target_principal=None, transaction_id=None):
        pass

    def subscribe_uufi(self):
        pass

    def publish(self, topic, payload_str):
        pass

    def start_handshake(self, device_id=None):
        self.handshake_transaction_id = f"UUFI:{self.source}:{self.generate_nonce()}"
        payload = {
            "setup": {
                "functions_ver": 9,
                "transaction_id": self.handshake_transaction_id,
                "msg_source": self.source,
                "user": self.principal
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

class ButlerMQTTBase(ButlerBusBase):
    def __init__(self, source, conn_spec=None, track_nonces=True):
        super().__init__(source, conn_spec, track_nonces)
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_mqtt_message

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            print(f"[{self.source}] Successfully connected to MQTT broker at {self.host}:{self.port_or_topic}")
            self.on_connect()
        else:
            print(f"Connection failed with code {rc}")

    def _on_mqtt_message(self, client, userdata, msg):
        try:
            data = json.loads(msg.payload.decode())
            parts = msg.topic.strip("/").split('/')
            
            try:
                uufi_idx = parts.index("uufi")
            except ValueError:
                return

            if len(parts) < uufi_idx + 3:
                return

            branch = parts[uufi_idx + 1]
            if branch == "r":
                registry_id = parts[uufi_idx + 2]
                device_id = parts[uufi_idx + 4]
                sub_type = parts[uufi_idx + 5]
                sub_folder = parts[uufi_idx + 6] if len(parts) > uufi_idx + 6 else None
            elif branch in ["p", "c"]:
                sub_type = parts[uufi_idx + 3]
                sub_folder = parts[uufi_idx + 4] if len(parts) > uufi_idx + 4 else None
                device_id = None
            else:
                return

            udmi_payload = data.get("payload", {})
            for k, v in data.items():
                if k != "payload":
                    udmi_payload[k] = v
            
            self._handle_received_message(msg.topic, device_id, sub_type, sub_folder, udmi_payload)
        except Exception:
            pass

    def connect(self):
        self.client.connect(self.host, self.port_or_topic, 60)

    def loop_start(self):
        self.client.loop_start()

    def loop_forever(self):
        self.client.loop_forever()

    def publish_uufi(self, device_id, sub_type, payload_data, sub_folder=None, direction="reflect", target_principal=None, transaction_id=None):
        publish_time = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        nonce = self.generate_nonce()
        is_handshake = (sub_folder == "udmi")

        timestamp = payload_data.get("timestamp", publish_time)
        version = payload_data.get("version", "1.5.2")
        
        if sub_folder and sub_folder not in payload_data:
            wrapped_payload = { "timestamp": timestamp, "version": version, sub_folder: payload_data }
        else:
            wrapped_payload = payload_data
            wrapped_payload.setdefault("timestamp", timestamp)
            wrapped_payload.setdefault("version", version)

        envelope = {
            "projectId": self.project_id,
            "transactionId": transaction_id or f"UUFI:{self.source}:{nonce}",
            "publishTime": publish_time,
            "source": self.source,
            "nonce": nonce,
            "payload": wrapped_payload
        }
        
        topic_parts = [self.prefix] if self.prefix else []
        topic_parts.append("uufi")
        if is_handshake:
            topic_parts.extend(["c", target_principal or self.principal])
        else:
            topic_parts.extend(["r", self.registry_id, "d", str(device_id) if device_id is not None else "all"])
        topic_parts.append(sub_type)
        if sub_folder:
            topic_parts.append(sub_folder)
        
        self.client.publish("/" + "/".join(topic_parts), json.dumps(envelope))

    def subscribe_uufi(self):
        topic = f"/{self.prefix}/#" if self.prefix else "/uufi/#"
        self.client.subscribe(topic)

    def publish(self, topic, payload_str):
        self.client.publish(topic, payload_str)

class ButlerPubSubBase(ButlerBusBase):
    def __init__(self, source, conn_spec=None, track_nonces=True):
        super().__init__(source, conn_spec, track_nonces)
        from google.cloud import pubsub_v1
        self.publisher = pubsub_v1.PublisherClient()
        self.subscriber = pubsub_v1.SubscriberClient()
        self.topic_path = self.publisher.topic_path(self.host, self.port_or_topic)
        self.sub_name = f"{self.port_or_topic}+{self.principal}"
        self.subscription_path = self.subscriber.subscription_path(self.host, self.sub_name)

    def connect(self):
        print(f"[{self.source}] PubSub initialized for project {self.host}, topic {self.port_or_topic}")
        try:
            self.subscriber.create_subscription(name=self.subscription_path, topic=self.topic_path)
        except Exception as e:
            # If it already exists, this is fine. Otherwise, log a note.
            if "already exists" not in str(e).lower():
                print(f"[{self.source}] Note: Using existing subscription or permission denied for creation: {e}")
        self.on_connect()

    def publish_uufi(self, device_id, sub_type, payload_data, sub_folder=None, direction="reflect", target_principal=None, transaction_id=None):
        publish_time = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        nonce = self.generate_nonce()
        is_handshake = (sub_folder == "udmi")

        timestamp = payload_data.get("timestamp", publish_time)
        version = payload_data.get("version", "1.5.2")
        
        if sub_folder and sub_folder not in payload_data:
            wrapped_payload = { "timestamp": timestamp, "version": version, sub_folder: payload_data }
        else:
            wrapped_payload = payload_data
            wrapped_payload.setdefault("timestamp", timestamp)
            wrapped_payload.setdefault("version", version)

        # PubSub Attributes (Envelope fields as per 4.1)
        attributes = {
            "projectId": self.project_id,
            "deviceRegistryId": "" if is_handshake else self.registry_id,
            "deviceId": "" if is_handshake else (device_id or ""),
            "subFolder": sub_folder or "",
            "subType": sub_type,
            "transactionId": transaction_id or f"UUFI:{self.source}:{nonce}",
            "publishTime": publish_time,
            "source": self.source,
            "nonce": nonce
        }
        
        p = target_principal or self.principal
        if p:
            attributes["principal"] = f"{p}@"

        data = json.dumps(wrapped_payload).encode("utf-8")
        self.publisher.publish(self.topic_path, data, **attributes)

    def _callback(self, message):
        try:
            udmi_payload = json.loads(message.data.decode("utf-8"))
            attributes = dict(message.attributes)
            
            # Merge attributes for compatibility
            for k, v in attributes.items():
                udmi_payload[k] = v
                
            device_id = attributes.get("deviceId")
            sub_type = attributes.get("subType")
            sub_folder = attributes.get("subFolder")
            
            # Principal filtering as per 2.1
            target_principal = attributes.get("principal")
            if self.filter_principal and target_principal and target_principal != f"{self.principal}@":
                message.ack()
                return

            self._handle_received_message(self.topic_path, device_id, sub_type, sub_folder, udmi_payload)
            message.ack()
        except Exception:
            message.nack()

    def subscribe_uufi(self):
        self.subscriber.subscribe(self.subscription_path, callback=self._callback)

    def publish(self, topic, payload_str):
        # For PubSub, we'll treat 'topic' as a topic name within the project if it's not the default path
        # But for simplicity and matching the MQTT usage 'butler/verify', 
        # we'll just publish to the default topic path but maybe add an attribute for the 'target topic'
        # Or better: if topic looks like a full path, use it, else assume it's a topic name.
        if "/" in topic:
            target_topic = self.publisher.topic_path(self.host, topic.replace("/", "_"))
        else:
            target_topic = self.publisher.topic_path(self.host, topic)
        
        try:
            self.publisher.publish(target_topic, payload_str.encode("utf-8"))
        except Exception:
            # Topic might not exist, in a real system we'd handle this
            pass

    def loop_forever(self):
        while True:
            time.sleep(1)

def ButlerBusFactory(source, conn_spec, track_nonces=True):
    scheme, _, _, _, _ = parse_conn_spec(conn_spec)
    if scheme == "pubsub":
        return ButlerPubSubBase(source, conn_spec, track_nonces)
    return ButlerMQTTBase(source, conn_spec, track_nonces)
