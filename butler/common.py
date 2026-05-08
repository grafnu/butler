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

        # 2. Protocol Mapping & Debug differentiation
        if user and "." in user:
             # Tool MUST throw an error if a manual differentiator is detected.
             raise ValueError(f"Manual differentiator detected in user component: {user}")

        if scheme == "pubsub" and port_or_topic and ":" in str(port_or_topic):
             # The :port component is NOT allowed for pubsub:// URLs.
             raise ValueError(f"Port component not allowed for pubsub: {port_or_topic}")

        # Differentiator suffix
        suffix = "" if source == "butler" else f".{source}"

        self.scheme = scheme
        self.user = (user or "unknown") + suffix
        self.principal = self.user + ("@" if scheme == "pubsub" else "")
        self.host = host # project_id for pubsub
        self.port_or_topic = port_or_topic
        self.prefix = prefix

        self.handshake_complete = False
        self.handshake_transaction_id = None
        self.project_id = os.environ.get("BUTLER_PROJECT_ID", "vibrant")
        self.registry_id = os.environ.get("BUTLER_REGISTRY_ID", "controller")
        self.track_nonces = track_nonces
        self.seen_nonces = {} # nonce -> timestamp
        self.nonce_window = 300 # 5 minutes
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
                now = time.time()
                # Clean old nonces
                self.seen_nonces = {n: t for n, t in self.seen_nonces.items() if now - t < self.nonce_window}
                if nonce in self.seen_nonces:
                    return
                self.seen_nonces[nonce] = now

        # Handshake check (look inside 'udmi' subfolder)
        if sub_type == "config" and sub_folder == "udmi":
            udmi_block = udmi_payload.get("udmi", {})
            reply = udmi_block.get("reply")
            if reply and reply.get("transaction_id") == self.handshake_transaction_id:
                setup = udmi_block.get("setup", {})
                new_registry_id = setup.get("registry_id")
                if new_registry_id:
                    self.registry_id = new_registry_id
                self.handshake_complete = True
                print(f"[{self.source}] Handshake complete! (Registry: {self.registry_id})")

        self.on_message(topic, device_id, sub_type, sub_folder, udmi_payload)

    def on_connect(self):
        pass

    def on_message(self, topic, device_id, sub_type, sub_folder, data):
        pass

    def on_raw_message(self, topic, data):
        pass

    def connect(self):
        pass

    def loop_start(self):
        pass

    def loop_forever(self):
        pass

    def publish_uufi(self, device_id, sub_type, payload_data, sub_folder=None, direction="reflect", target_principal=None, transaction_id=None, registry_id=None):
        pass

    def subscribe_uufi(self):
        pass

    def publish(self, topic, payload_str):
        pass

    def _wrap_payload(self, payload_data, sub_folder):
        publish_time = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        timestamp = payload_data.get("timestamp", publish_time)
        version = payload_data.get("version", "1.5.2")
        
        if not sub_folder:
            res = payload_data.copy()
            res.setdefault("timestamp", timestamp)
            res.setdefault("version", version)
            return res, publish_time

        if sub_folder in payload_data:
            inner_data = payload_data[sub_folder]
        else:
            inner_data = payload_data.copy()
            # We can optionally remove timestamp/version from the copy if we want a very clean inner payload,
            # but it's safer to keep them if they might be part of the actual data (like firmware version).
            # To be strict with 9.1's "exactly one top-level key", we MUST remove them from the WRAPPED payload's top level
            # which we already do by creating a new 'wrapped' dict.
            pass
            
        wrapped = {
            "timestamp": timestamp,
            "version": version,
            sub_folder: inner_data
        }
        return wrapped, publish_time

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
            payload_str = msg.payload.decode()
            try:
                data = json.loads(payload_str)
                parts = msg.topic.strip("/").split('/')
                
                try:
                    uufi_idx = parts.index("uufi")
                except ValueError:
                    # Non-UUFI JSON message
                    self.on_raw_message(msg.topic, payload_str)
                    return

                # Unified Structure: /uufi/[r/{registryId}/[d/{deviceId}/]|p/{principal}/]c/{subType}/{subFolder}
                remaining = parts[uufi_idx + 1:]
                registry_id = None
                device_id = None
                principal = None
                
                if not remaining:
                    self.on_raw_message(msg.topic, payload_str)
                    return

                curr = 0
                if remaining[curr] == "r":
                    registry_id = remaining[curr+1]
                    curr += 2
                    if remaining[curr] == "d":
                        device_id = remaining[curr+1]
                        curr += 2
                elif remaining[curr] == "p":
                    principal = remaining[curr+1]
                    curr += 2
                
                if remaining[curr] != "c":
                    self.on_raw_message(msg.topic, payload_str)
                    return
                
                sub_type = remaining[curr+1]
                sub_folder = remaining[curr+2] if len(remaining) > curr + 2 else None

                udmi_payload = data.get("payload", {})
                for k, v in data.items():
                    if k != "payload":
                        udmi_payload[k] = v
                
                if registry_id:
                    udmi_payload["deviceRegistryId"] = registry_id
                if device_id:
                    udmi_payload["deviceId"] = device_id
                if principal:
                    udmi_payload["principal"] = principal

                self._handle_received_message(msg.topic, device_id, sub_type, sub_folder, udmi_payload)
            except (json.JSONDecodeError, IndexError):
                self.on_raw_message(msg.topic, payload_str)
        except Exception:
            pass

    def on_raw_message(self, topic, payload):
        pass

    def connect(self):
        self.client.connect(self.host, self.port_or_topic, 60)

    def loop_start(self):
        self.client.loop_start()

    def loop_forever(self):
        self.client.loop_forever()

    def publish_uufi(self, device_id, sub_type, payload_data, sub_folder=None, direction="reflect", target_principal=None, transaction_id=None, registry_id=None):
        nonce = self.generate_nonce()
        wrapped_payload, publish_time = self._wrap_payload(payload_data, sub_folder)

        envelope = {
            "projectId": self.project_id,
            "transactionId": transaction_id or f"UUFI:{self.source}:{nonce}",
            "publishTime": publish_time,
            "source": self.source,
            "nonce": nonce,
            "payload": wrapped_payload,
            "principal": self.principal
        }
        
        topic_parts = [self.prefix] if self.prefix else []
        topic_parts.append("uufi")
        
        # Unified Structure: /uufi/[r/{registryId}/[d/{deviceId}/]|p/{principal}/]c/{subType}/{subFolder}
        if registry_id:
            topic_parts.extend(["r", registry_id])
            if device_id:
                topic_parts.extend(["d", str(device_id)])
        else:
            p = target_principal or self.principal
            topic_parts.extend(["p", p])
        
        topic_parts.append("c")
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
        self.sub_name = f"{self.port_or_topic}+{self.user}"
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

    def publish_uufi(self, device_id, sub_type, payload_data, sub_folder=None, direction="reflect", target_principal=None, transaction_id=None, registry_id=None):
        nonce = self.generate_nonce()
        wrapped_payload, publish_time = self._wrap_payload(payload_data, sub_folder)
        is_handshake = (sub_folder == "udmi")

        # PubSub Attributes (Envelope fields as per 4.1)
        attributes = {
            "projectId": self.project_id,
            "deviceRegistryId": registry_id or ("" if is_handshake else self.registry_id),
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
            # Ensure the principal matches the required format (toolname@)
            attributes["principal"] = p if "@" in p else f"{p}@"

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
            if self.filter_principal and target_principal and target_principal != self.principal:
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
