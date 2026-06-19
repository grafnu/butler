import json
import time
import os
import sys
import threading
import secrets
import datetime
import paho.mqtt.client as mqtt
from butler.messaging import parse_message
from butler.conn_spec import match_principal

class PublishResult:
    def __init__(self, result):
        self.result = result

    def wait(self, timeout=None):
        if hasattr(self.result, 'wait_for_publish'):
            # MQTT
            self.result.wait_for_publish(timeout)
        elif hasattr(self.result, 'result'):
            # PubSub Future
            self.result.result(timeout)
        else:
            # Fallback
            pass

class Transport:
    def connect(self): raise NotImplementedError()
    def publish(self, envelope, payload): raise NotImplementedError()
    def subscribe(self, callback): raise NotImplementedError()
    def loop_start(self): pass
    def loop_stop(self): pass
    @property
    def is_connected(self): return True

class MqttTransport(Transport):
    def __init__(self, conn_spec):
        self.conn_spec = conn_spec
        self._is_v2 = False
        
        # Unique Client ID (UUFI Section 8.4: incorporate prefix or a random nonce)
        prefix_str = f"{conn_spec.username or 'uufi'}"
        client_id = f"{prefix_str}-{secrets.token_hex(4)}"
        if conn_spec.prefix:
            client_id = f"{conn_spec.prefix.replace('/', '-')}-{client_id}"

        try:
            # Try for paho-mqtt 2.0.0+ API
            self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
            self._is_v2 = True
        except AttributeError:
            # Fallback for older versions
            try:
                self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id=client_id)
            except AttributeError:
                self.client = mqtt.Client(client_id=client_id)
        self.callback = None
        self.on_connect_callback = None
        self._is_connected = False
        self.subscriptions = []
        self.seen_transactions = {}
        self.queue = []
        self.queue_lock = threading.Lock()
        self.queue_event = threading.Event()
        self.worker_thread = threading.Thread(target=self._worker, daemon=True)
        self.worker_thread.start()

    def _worker(self):
        while True:
            self.queue_event.wait()
            while True:
                with self.queue_lock:
                    if not self.queue:
                        self.queue_event.clear()
                        break
                    args = self.queue.pop(0)
                try:
                    self.callback(*args)
                except Exception as e:
                    print(f"Error in transport callback: {e}", file=sys.stderr)

    @property
    def is_connected(self):
        return self._is_connected

    def connect(self):
        host = self.conn_spec.host
        port = self.conn_spec.port or 1883
        
        if port != 1883:
            self.client.username_pw_set("rocket", "monkey")
        elif self.conn_spec.username:
            password = getattr(self.conn_spec, 'password', None)
            self.client.username_pw_set(self.conn_spec.username, password)
        
        # Check if certs exist and port is not 1883
        if port != 1883:
            workspace_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            # Always prefer sibling/peer udmi/var/mosquitto/certs first
            peer_udmi_certs = os.path.join(workspace_root, "..", "udmi", "var", "mosquitto", "certs")
            ca_file = os.path.join(peer_udmi_certs, "ca.crt")
            cert_file = os.path.join(peer_udmi_certs, "rsa_private.crt")
            key_file = os.path.join(peer_udmi_certs, "rsa_private.pem")
            
            if not (os.path.exists(ca_file) and os.path.exists(cert_file) and os.path.exists(key_file)):
                # Try local symlink udmi/var/mosquitto/certs
                symlink_certs = os.path.join(workspace_root, "udmi", "var", "mosquitto", "certs")
                ca_file = os.path.join(symlink_certs, "ca.crt")
                cert_file = os.path.join(symlink_certs, "rsa_private.crt")
                key_file = os.path.join(symlink_certs, "rsa_private.pem")
                
                if not (os.path.exists(ca_file) and os.path.exists(cert_file) and os.path.exists(key_file)):
                    # Fallback to local workspace var/mosquitto/certs
                    ca_file = os.path.join(workspace_root, "var", "mosquitto", "certs", "ca.crt")
                    cert_file = os.path.join(workspace_root, "var", "mosquitto", "certs", "rsa_private.crt")
                    key_file = os.path.join(workspace_root, "var", "mosquitto", "certs", "rsa_private.pem")
            
            if os.path.exists(ca_file) and os.path.exists(cert_file) and os.path.exists(key_file):
                self.client.tls_set(ca_certs=ca_file, certfile=cert_file, keyfile=key_file)
                self.client.tls_insecure_set(True)

        if self._is_v2:
            self.client.on_connect = self._on_connect_v2
        else:
            self.client.on_connect = self._on_connect_v1
            
        self.client.on_message = self.on_message
        self.client.connect(host, port, 60)

    def _on_connect_v1(self, client, userdata, flags, rc):
        if rc == 0:
            self._is_connected = True
            for topic in self.subscriptions:
                self.client.subscribe(topic)
            if self.on_connect_callback:
                self.on_connect_callback()
        else:
            self._is_connected = False
            print(f"MQTT connect failed: {rc}", flush=True)

    def _on_connect_v2(self, client, userdata, flags, reason_code, properties=None):
        if reason_code == 0:
            self._is_connected = True
            for topic in self.subscriptions:
                self.client.subscribe(topic)
            if self.on_connect_callback:
                self.on_connect_callback()
        else:
            self._is_connected = False
            print(f"MQTT connect failed: {reason_code}", flush=True)

    def on_message(self, client, userdata, msg):
        if not self.callback: return
        
        # UUFI Section 8.3: All UUFI topics MUST start with a leading slash '/'.
        if not msg.topic.startswith('/'):
            return 
        
        raw_payload = msg.payload.decode('utf-8', errors='replace')
        data = parse_message(msg.payload)
        
        if not data or not isinstance(data, dict):
            return  # Reject non-JSON or non-object payloads

        # UUFI Section 10.1 (Schema): MQTT envelope MUST include these fields
        mandatory = ["projectId", "publishTime", "source", "principal", "payload", "transactionId", "nonce"]
        for field in mandatory:
            if field not in data:
                sys.stderr.write(f"Protocol Violation: Missing mandatory field '{field}' in MQTT envelope from {msg.topic}\n")
                return # Reject message missing mandatory MQTT envelope field
        
        payload = data.get("payload")
        env = {k: data[k] for k in mandatory if k != "payload"}
        
        # UUFI Section 10.1: deviceRegistryId/deviceId may be in envelope if not in topic path
        if "deviceRegistryId" in data: env["deviceRegistryId"] = data["deviceRegistryId"]
        if "deviceId" in data: env["deviceId"] = data["deviceId"]

        # Principal filtering (UUFI Section 8.6)
        msg_principal = env.get("principal")
        if not msg_principal:
            sys.stderr.write(f"Protocol Violation: Missing principal in MQTT envelope from {msg.topic}\n")
            return # UUFI 8.6: If principal missing from MQTT envelope, MUST reject.

        source = env.get("source")
        # Self-message filtering (UUFI Section 7.4)
        if source == self.conn_spec.source_id:
            return

        # Parse topic to extract envelope
        # Structure: [/{prefix}]/uufi/[r/{registryId}/[d/{deviceId}/]]c/{subType}/{subFolder}
        topic_path = msg.topic.lstrip('/')
        parts = topic_path.split('/')
        
        try:
            uufi_idx = parts.index("uufi")
        except ValueError:
            return

        # Prefix Isolation (UUFI Section 2.2 / 8.3)
        expected_prefix = self.conn_spec.prefix
        if expected_prefix:
            expected_parts = expected_prefix.strip('/').split('/')
            if uufi_idx != len(expected_parts) or parts[:uufi_idx] != expected_parts:
                return # Prefix mismatch or missing
        elif uufi_idx > 0:
            return # Prefix present but none expected

        rem = parts[uufi_idx + 1:]
        
        topic_env = {}
        if "c" in rem:
            c_idx = rem.index("c")
            if c_idx >= 2:
                if rem[0] == "r":
                    topic_env["deviceRegistryId"] = rem[1]
                    if c_idx >= 4 and rem[2] == "d":
                        topic_env["deviceId"] = rem[3]
            
            if len(rem) > c_idx + 2:
                topic_env["subType"] = rem[c_idx + 1]
                topic_env["subFolder"] = rem[c_idx + 2]
        else:
            return # Not a standard UUFI topic structure

        # UUFI Section 8.3: Redundancy Rule
        for field in ["subType", "subFolder", "deviceRegistryId", "deviceId"]:
            if field in data and field in topic_env:
                sys.stderr.write(f"Protocol Violation: Redundant field '{field}' in envelope from {msg.topic}\n")
                return  # Reject message containing redundant envelope fields

        if self.conn_spec.principal and not self.conn_spec.is_passive:
            if not match_principal(msg_principal, self.conn_spec.principal):
                return

        env.update(topic_env)
        tid = env.get("transactionId")
        nonce = env.get("nonce")
        sub_folder = env.get("subFolder")

        # Deduplication (UUFI Section 7.3 & 9): Use nonce if present, else transactionId
        # Handshake messages (udmi subfolder) should bypass deduplication.
        dedup_id = nonce or tid
        if dedup_id and sub_folder != "udmi":
            now = time.time()
            if dedup_id in self.seen_transactions and (now - self.seen_transactions[dedup_id]) < 300:
                return
            self.seen_transactions[dedup_id] = now
            
            # Simple cleanup to prevent unbounded memory growth
            if len(self.seen_transactions) > 100:
                self.seen_transactions = {k: v for k, v in self.seen_transactions.items() if (now - v) < 300}

        # UUFI Section 3: MQTT message callback handlers MUST NOT perform long-running or blocking operations.
        with self.queue_lock:
            self.queue.append((env, payload, msg.topic, raw_payload))
            self.queue_event.set()

    def publish(self, envelope, payload):
        # Wait for the MQTT connection to be fully established (up to 5 seconds)
        start_wait = time.time()
        while not self._is_connected and (time.time() - start_wait) < 5.0:
            time.sleep(0.1)

        topic = self.get_topic(envelope)

        # Ensure transactionId is present and consistent (UUFI Section 7.1)
        tid = envelope.get("transactionId") or envelope.get("transaction_id") or secrets.token_hex(4)
        nonce = envelope.get("nonce") or secrets.token_hex(4)
        envelope["transactionId"] = tid
        envelope["nonce"] = nonce

        # Prepare wrapped payload for MQTT (UUFI Section 4.1)
        wrapped = {"payload": payload}
        
        # Mandatory fields for MQTT envelope
        wrapped["projectId"] = envelope.get("projectId") or self.conn_spec.project_id or "vibrant"
        wrapped["transactionId"] = tid
        wrapped["nonce"] = nonce
        wrapped["source"] = envelope.get("source") or self.conn_spec.source_id
        wrapped["principal"] = envelope.get("principal") or self.conn_spec.principal or "unknown"
        
        # UUFI Section 8.4: Redundancy Rule
        # Only include deviceId/deviceRegistryId in envelope if they are NOT in the topic path.
        if "deviceRegistryId" in envelope and "r" not in topic.split('/'):
             wrapped["deviceRegistryId"] = envelope["deviceRegistryId"]
        if "deviceId" in envelope and "d" not in topic.split('/'):
             wrapped["deviceId"] = envelope["deviceId"]

        if "publishTime" in envelope:
            wrapped["publishTime"] = envelope["publishTime"]
        else:
            now = datetime.datetime.now(datetime.timezone.utc)
            wrapped["publishTime"] = now.strftime('%Y-%m-%dT%H:%M:%SZ')

        # UUFI Section 8.4: Redundancy Rule: Envelope fields MUST NOT include data encoded in the topic path
        # Our get_topic implementation encodes subType, subFolder, and (if present) 
        # deviceRegistryId and deviceId. Thus, we MUST NOT include them in the JSON envelope.
        return PublishResult(self.client.publish(topic, json.dumps(wrapped), qos=1))

    def get_topic(self, env):
        parts = []
        if self.conn_spec.prefix:
            parts.append(self.conn_spec.prefix)
        parts.append("uufi")
            
        registry_id = env.get("deviceRegistryId") or env.get("registry_id")
        if registry_id:
            parts.extend(["r", registry_id])
            device_id = env.get("deviceId") or env.get("device_id")
            if device_id:
                parts.extend(["d", device_id])
        
        parts.append("c")
        sub_type = env.get("subType") or env.get("sub_type") or "unknown"
        sub_folder = env.get("subFolder") or env.get("sub_folder") or "unknown"
        parts.extend([sub_type, sub_folder])
            
        return "/" + "/".join(parts)

    def subscribe(self, topic, callback):
        if not topic.startswith('/'):
            print(f"Warning: Rejecting subscription to topic without leading slash: {topic}")
            return
        
        # Prefix Isolation (UUFI Section 8.4)
        # All active subscriptions MUST be scoped to the provided prefix.
        if self.conn_spec.prefix:
            prefix_part = f"/{self.conn_spec.prefix}/"
            if not topic.startswith(prefix_part):
                print(f"Warning: Rejecting subscription outside prefix tree ({prefix_part}): {topic}")
                return
        else:
            if not topic.startswith("/uufi/"):
                print(f"Warning: Rejecting subscription outside /uufi/ tree: {topic}")
                return

        if topic not in self.subscriptions:
            self.subscriptions.append(topic)
        self.callback = callback
        self.client.subscribe(topic)

    def loop_start(self):
        self.client.loop_start()
    
    def loop_stop(self):
        self.client.loop_stop()

class PubSubTransport(Transport):
    def __init__(self, conn_spec):
        self.conn_spec = conn_spec
        from google.cloud import pubsub_v1
        self.publisher = pubsub_v1.PublisherClient()
        self.subscriber = pubsub_v1.SubscriberClient()
        self.callback = None
        self.project_id = conn_spec.project_id
        self.root_topic = conn_spec.root_topic
        self.subscription_path = self.subscriber.subscription_path(self.project_id, conn_spec.subscription)
        self.topic_path = self.publisher.topic_path(self.project_id, self.root_topic)
        self.seen_transactions = {}

    def connect(self):
        pass # PubSub is serverless

    def publish(self, envelope, payload):
        attributes = {}
        for k, v in envelope.items():
            if k != "payload" and v is not None:
                attributes[k] = str(v)
        
        if "projectId" not in attributes:
            attributes["projectId"] = self.project_id or "vibrant"

        if "nonce" not in attributes:
            attributes["nonce"] = secrets.token_hex(4)

        # In PubSub, the principal attribute might need special handling
        if self.conn_spec.principal and "principal" not in attributes:
            attributes["principal"] = self.conn_spec.principal

        data = json.dumps(payload).encode("utf-8")
        return PublishResult(self.publisher.publish(self.topic_path, data, **attributes))

    def subscribe(self, callback):
        self.callback = callback
        
        def wrapped_callback(message):
            env = dict(message.attributes)
            payload = parse_message(message.data)
            
            source = env.get("source")
            # Self-message filtering (UUFI Section 7.4)
            if source == self.conn_spec.source_id:
                message.ack()
                return

            # Deduplication (UUFI Section 7 / Butler Section 8)
            # Section 7.3: Implementations MUST NOT reject messages from self when deduping
            tid = env.get("transactionId") or env.get("transaction_id")
            nonce = env.get("nonce")
            sub_folder = env.get("subFolder")
            
            dedup_id = nonce or tid
            if dedup_id and sub_folder != "udmi":
                now = time.time()
                if dedup_id in self.seen_transactions and (now - self.seen_transactions[dedup_id]) < 300:
                    message.ack()
                    return
                self.seen_transactions[dedup_id] = now
                if len(self.seen_transactions) > 100:
                    self.seen_transactions = {k: v for k, v in self.seen_transactions.items() if (now - v) < 300}

            # Filtering: Only include messages that have matching principal or attribute missing
            msg_principal = env.get("principal")

            if msg_principal and self.conn_spec.principal and not self.conn_spec.is_passive:
                if not match_principal(msg_principal, self.conn_spec.principal):
                    message.nack()
                    return
                    
                    # Note: Handshake messages (udmi/config) now also allow base matching 
                    # to account for identity differentiators (UUFI Section 3).
            
            self.callback(env, payload, self.subscription_path, message.data.decode('utf-8', errors='replace'))
            message.ack()

        self.streaming_pull_future = self.subscriber.subscribe(self.subscription_path, callback=wrapped_callback)

    def loop_stop(self):
        if hasattr(self, 'streaming_pull_future'):
            self.streaming_pull_future.cancel()

def get_transport(conn_spec):
    if conn_spec.protocol == "pubsub":
        return PubSubTransport(conn_spec)
    return MqttTransport(conn_spec)
