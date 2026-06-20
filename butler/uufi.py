import os
import json
import time
import uuid
import sys
import threading
import paho.mqtt.client as mqtt
from datetime import datetime, timezone

def get_timestamp():
    # Return RFC 3339 formatted UTC timestamp with minimal precision (no fractional seconds)
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

class UUFIClient:
    def __init__(self, conn_spec, entity_suffix):
        from butler.utils import parse_conn_spec
        self.conn_spec = parse_conn_spec(conn_spec, entity_suffix)
        self.client_id = f"uufi_{self.conn_spec['principal']}_{uuid.uuid4().hex[:6]}"
        self.mqtt_client = mqtt.Client(client_id=self.client_id)
        
        # Configure credentials if present (for local mock start_local, etc.)
        # Default: rocket / monkey
        self.mqtt_client.username_pw_set("rocket", "monkey")
        
        self.prefix = self.conn_spec["prefix"]
        self.principal = self.conn_spec["principal"]
        self.implementation_id = self.conn_spec["implementation_id"]
        
        self.connected = False
        self.connect_cond = threading.Condition()
        
        # Deduplication cache: transaction_id -> timestamp of insertion
        self.dedup_cache = {}
        self.dedup_lock = threading.Lock()
        
        # Setup callbacks
        self.mqtt_client.on_connect = self._on_connect
        self.mqtt_client.on_disconnect = self._on_disconnect
        self.mqtt_client.on_message = self._on_message
        
        self.callbacks = {} # topic_pattern -> callback list
        self.handshake_completed = False
        self.handshake_event = threading.Event()
        
        # Start cleanup thread for deduplication
        self.running = True
        self.cleanup_thread = threading.Thread(target=self._dedup_cleanup_loop, daemon=True)
        self.cleanup_thread.start()

    def _dedup_cleanup_loop(self):
        while self.running:
            time.sleep(30)
            now = time.time()
            with self.dedup_lock:
                # Remove items older than 5 minutes (300 seconds)
                expired = [tid for tid, t in self.dedup_cache.items() if now - t > 300]
                for tid in expired:
                    del self.dedup_cache[tid]

    def is_duplicate(self, transaction_id):
        if not transaction_id:
            return False
        now = time.time()
        with self.dedup_lock:
            if transaction_id in self.dedup_cache:
                return True
            self.dedup_cache[transaction_id] = now
            return False

    def build_topic(self, sub_type, sub_folder, site_id=None, device_id=None):
        # Topic Suffix Standard Formatting (Section 12.7)
        if not sub_type or not sub_folder:
            raise ValueError("Both sub_type and sub_folder must be provided to build a compliant UUFI topic path.")
        # Topic Slashes: All UUFI topics MUST start with a leading slash `/`.
        # [/{prefix}]/uufi/[r/{deviceRegistryId}/[d/{deviceId}/]]c/{subType}/{subFolder}
        parts = []
        if self.prefix:
            parts.append(self.prefix)
        parts.append("uufi")
        
        if site_id and device_id:
            parts.extend(["r", site_id, "d", device_id])
            
        parts.extend(["c", sub_type, sub_folder])
        return "/" + "/".join(parts)

    def parse_topic(self, topic):
        # Parses site_id, device_id, sub_type, sub_folder from incoming topic
        # Must handle prefixed topics cleanly
        topic_stripped = topic.lstrip("/")
        if self.prefix and topic_stripped.startswith(self.prefix + "/"):
            topic_stripped = topic_stripped[len(self.prefix)+1:]
            
        parts = topic_stripped.split("/")
        # Expected parts: uufi/[r/{site_id}/d/{device_id}/]c/{sub_type}/{sub_folder}
        if not parts or parts[0] != "uufi":
            return None
            
        site_id = None
        device_id = None
        
        if len(parts) >= 8 and parts[1] == "r" and parts[3] == "d":
            site_id = parts[2]
            device_id = parts[4]
            sub_type = parts[6]
            sub_folder = parts[7]
        elif len(parts) >= 4 and parts[1] == "c":
            sub_type = parts[2]
            sub_folder = parts[3]
        else:
            return None
            
        return {
            "site_id": site_id,
            "device_id": device_id,
            "sub_type": sub_type,
            "sub_folder": sub_folder
        }

    def connect(self):
        # Enable SSL if start_local port 8883 is used
        # We can look for ca.crt in standard testing site model
        if self.conn_spec["port"] == 8883:
            ca_path = "testing/udmi_site_model/reflector/ca.crt"
            if not os.path.exists(ca_path):
                ca_path = "udmi_blob_store/ca.crt" # fallback
            if os.path.exists(ca_path):
                self.mqtt_client.tls_set(ca_certs=ca_path)
                self.mqtt_client.tls_insecure_set(True) # ignore name validation for localhost testing

        self.mqtt_client.connect_async(self.conn_spec["host"], self.conn_spec["port"], 60)
        self.mqtt_client.loop_start()
        
        with self.connect_cond:
            while not self.connected:
                if not self.connect_cond.wait(timeout=5):
                    # Connection timeout
                    print(f"Failed to connect to MQTT broker at {self.conn_spec['host']}:{self.conn_spec['port']}", file=sys.stderr)
                    return False
        return True

    def disconnect(self):
        self.running = False
        self.mqtt_client.loop_stop()
        self.mqtt_client.disconnect()

    def _on_connect(self, client, userdata, flags, rc):
        with self.connect_cond:
            if rc == 0:
                self.connected = True
                print(f"Connected to MQTT broker at {self.conn_spec['host']}:{self.conn_spec['port']}", file=sys.stderr)
                # Resubscribe to callbacks
                for topic in self.callbacks:
                    self.mqtt_client.subscribe(topic, qos=1)
            else:
                print(f"MQTT connection failed with code {rc}", file=sys.stderr)
            self.connect_cond.notify_all()

    def _on_disconnect(self, client, userdata, rc):
        with self.connect_cond:
            self.connected = False
            print("Disconnected from MQTT broker", file=sys.stderr)

    def _on_message(self, client, userdata, msg):
        # MUST NOT block!
        # Run in a new thread to avoid blocking MQTT loop
        t = threading.Thread(target=self._handle_message_thread, args=(msg,), daemon=True)
        t.start()

    def _handle_message_thread(self, msg):
        try:
            topic = msg.topic
            payload_str = msg.payload.decode("utf-8")
            envelope = json.loads(payload_str)
        except Exception as e:
            print(f"Failed to parse incoming message on {msg.topic}: {e}", file=sys.stderr)
            return

        # Check deduplication on incoming Model Update and Command/Config messages
        # DO NOT discard Device State reports (which are state/blobset or state/udmi)
        topic_info = self.parse_topic(topic)
        if topic_info:
            sub_type = topic_info["sub_type"]
            is_state = (sub_type == "state")
            transaction_id = envelope.get("transactionId")
            
            if not is_state and transaction_id:
                if self.is_duplicate(transaction_id):
                    # Silently ignore duplicate
                    return

        # Find matching callbacks
        matched_callbacks = []
        for pattern, cb_list in self.callbacks.items():
            if mqtt.topic_matches_sub(pattern, topic):
                matched_callbacks.extend(cb_list)
                
        for cb in matched_callbacks:
            try:
                cb(topic, envelope)
            except Exception as e:
                print(f"Error in callback for {topic}: {e}", file=sys.stderr)

    def subscribe(self, topic_pattern, callback):
        # Topic Slashes: All UUFI topics MUST start with a leading slash `/`
        if not topic_pattern.startswith("/"):
            topic_pattern = "/" + topic_pattern

        if topic_pattern not in self.callbacks:
            self.callbacks[topic_pattern] = []
            if self.connected:
                self.mqtt_client.subscribe(topic_pattern, qos=1)
        self.callbacks[topic_pattern].append(callback)

    def publish(self, topic, inner_payload, transaction_id=None, recipient_principal=None):
        if not topic.startswith("/"):
            topic = "/" + topic

        if not transaction_id:
            transaction_id = f"UUFI:{self.principal}:{uuid.uuid4().hex[:8]}"

        # Standard MQTT envelope
        envelope = {
            "projectId": "vibrant",
            "transactionId": transaction_id,
            "publishTime": get_timestamp(),
            "source": self.principal,
            "principal": recipient_principal or self.principal,
            "payload": inner_payload
        }

        # Envelope Key Standardization:
        # subType and deviceRegistryId MUST NOT be in envelope if topic structure implies them
        # We strip them in our outer envelope construction by only keeping projectId, transactionId, publishTime, source, principal, payload.
        # This matches the redundancy rule perfectly.

        payload_str = json.dumps(envelope)
        self.mqtt_client.publish(topic, payload_str.encode("utf-8"), qos=1)

    # Handshake - Client Side (for Verifier / Device if simulated)
    def initiate_handshake(self, timeout=60):
        # Step 1: Publish state/udmi
        state_topic = self.build_topic("state", "udmi")
        transaction_id = f"UUFI:{self.principal}:{uuid.uuid4().hex[:8]}"
        
        state_payload = {
            "version": "1.5.2",
            "timestamp": get_timestamp(),
            "setup": {
                "functions_ver": 9,
                "transaction_id": transaction_id,
                "msg_source": self.principal
            }
        }

        # Subscribe to config/udmi config response
        config_topic = self.build_topic("config", "udmi")
        
        def on_handshake_reply(topic, envelope):
            # Handshake Request and Reply Payload Formatting (Section 12.1)
            # Enforce that the handshake config reply's envelope includes transactionId matching original request's transaction ID
            reply_tx_id = envelope.get("transactionId") or envelope.get("transaction_id")
            if reply_tx_id != transaction_id:
                # Reject handshake replies that do not match their original request's transaction ID
                return

            payload = envelope.get("payload", {})
            # Flattened format (Section 12.1): 'reply' block resides directly at the payload root
            reply = payload.get("reply")
            if not reply:
                # Fallback support if needed, but the main one must be flat
                reply = payload.get("setup", {}).get("reply", {})
            
            # Check envelope principal matches or target matches
            envelope_principal = envelope.get("principal")
            if envelope_principal == self.principal or not envelope_principal:
                if reply.get("transaction_id") == transaction_id or reply_tx_id == transaction_id:
                    self.handshake_completed = True
                    self.handshake_event.set()

        self.subscribe(config_topic, on_handshake_reply)
        
        start_time = time.time()
        while time.time() - start_time < timeout:
            print(f"Publishing Handshake Step 1 to {state_topic}...", file=sys.stderr)
            self.publish(state_topic, state_payload, transaction_id=transaction_id)
            if self.handshake_event.wait(timeout=5):
                print(f"Handshake completed successfully for {self.principal}!", file=sys.stderr)
                return True
                
        print(f"Handshake timed out after {timeout}s for {self.principal}", file=sys.stderr)
        return False

    # Handshake - System Side (for Butler)
    def setup_handshake_responder(self, on_client_active=None):
        # Butler is System, responds to Client handshake
        state_topic = self.build_topic("state", "udmi")
        
        def on_handshake_state(topic, envelope):
            # Parse handshake
            source = envelope.get("source")
            principal = envelope.get("principal") or source
            payload = envelope.get("payload", {})
            setup = payload.get("setup", {})
            
            # Extract transaction ID from client's request envelope or setup block
            transaction_id = envelope.get("transactionId") or envelope.get("transaction_id") or setup.get("transaction_id")
            
            if not principal or not transaction_id:
                return

            print(f"Received Handshake State from {principal}. Sending Step 2 Config Reply...", file=sys.stderr)
            
            config_topic = self.build_topic("config", "udmi")
            # Flattened format where "setup" and "reply" blocks reside directly at the payload root (Section 12.1)
            config_payload = {
                "version": "1.5.2",
                "timestamp": get_timestamp(),
                "setup": {
                    "functions_ver": 9,
                    "transaction_id": transaction_id,
                    "msg_source": self.principal,
                    "deviceRegistryId": "default" # Provide default registry ID
                },
                "reply": {
                    "transaction_id": transaction_id
                }
            }
            
            # Respond using principal or source as recipient_principal
            self.publish(config_topic, config_payload, transaction_id=transaction_id, recipient_principal=principal)
            
            if on_client_active:
                on_client_active(principal)

        self.subscribe(state_topic, on_handshake_state)
