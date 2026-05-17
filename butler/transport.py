import re
import urllib.parse
import os
from dataclasses import dataclass
from typing import Optional, Callable, Any, List, Dict
import json
import paho.mqtt.client as mqtt
import logging
import time
import uuid
from datetime import datetime, timezone

def get_default_conn_spec() -> str:
    return os.environ.get("BUTLER_CONN_SPEC", "mqtt://unknown@localhost/")

@dataclass
class ConnSpec:
    scheme: str
    host: str
    port: Optional[int]
    principal: Optional[str]
    prefix: Optional[str]

def parse_conn_spec(conn_spec_str: str) -> ConnSpec:
    # Handle the @ correctly according to UUFI spec 2.1
    # "The @ character is only allowed if it is preceded by a non-empty user identifier."
    # urlparse might strip the @ if it's considered part of the delimiter.

    parsed = urllib.parse.urlparse(conn_spec_str)

    scheme = parsed.scheme
    if scheme not in ["mqtt", "pubsub"]:
        raise ValueError(f"Unsupported scheme: {scheme}")

    # Manual check for @ in the netloc to see if it was provided
    netloc = parsed.netloc
    has_at = '@' in netloc

    principal = parsed.username or "unknown"
    if has_at and not principal.endswith('@') and scheme == 'pubsub':
        principal += '@'

    host = parsed.hostname
    port = parsed.port

    prefix = parsed.path.lstrip("/") if parsed.path else None

    return ConnSpec(
        scheme=scheme,
        host=host,
        port=port,
        principal=principal,
        prefix=prefix
    )


def get_timestamp() -> str:
    """Returns RFC 3339 minimal precision timestamp in UTC."""
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

def wrap_message(payload: dict, **envelope_kwargs) -> dict:
    msg = envelope_kwargs.copy()
    now = get_timestamp()

    if 'publishTime' not in msg:
        msg['publishTime'] = now
    if 'projectId' not in msg:
        msg['projectId'] = os.environ.get("BUTLER_PROJECT_ID", "vibrant")
    if 'transactionId' not in msg:
        msg['transactionId'] = uuid.uuid4().hex[:8]
    if 'nonce' not in msg:
        msg['nonce'] = uuid.uuid4().hex[:8]
    if 'principal' not in msg:
        msg['principal'] = msg.get('source', 'unknown')
    if 'source' not in msg:
        msg['source'] = msg.get('principal', 'unknown')

    if 'payload' not in msg:
        msg['payload'] = payload.copy()

    def normalize_versions(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k.endswith('_version') or k in ['version', 'current_version', 'target_version', 'lkg_version']:
                    if v is None:
                        obj[k] = "0.0.0"
                else:
                    normalize_versions(v)
        elif isinstance(obj, list):
            for item in obj:
                normalize_versions(item)

    normalize_versions(msg['payload'])

    if 'timestamp' not in msg['payload']:
        msg['payload']['timestamp'] = now
    if 'version' not in msg['payload']:
        msg['payload']['version'] = "1.5.2"

    return msg

def unwrap_message(msg: dict) -> dict:
    if isinstance(msg, dict) and 'payload' in msg:
        return msg['payload']
    return msg

class MqttTransport:
    def __init__(self, conn_spec: ConnSpec, tag: str = None, passive: bool = False):
        self.conn_spec = conn_spec
        self.passive = passive
        # Apply tag differentiator if provided
        if tag and tag != "butler": # butler is default
            if self.conn_spec.principal.endswith('@'):
                self.principal = self.conn_spec.principal[:-1] + "." + tag + "@"
            else:
                self.principal = self.conn_spec.principal + "." + tag
        else:
            self.principal = self.conn_spec.principal

        self.client = mqtt.Client()
        self.on_message_callback: Optional[Callable[[str, Any], None]] = None
        self.seen_nonces: List[Dict] = [] # List of {'nonce': str, 'time': float}

        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self._connected = False

    def connect(self):
        host = self.conn_spec.host
        port = self.conn_spec.port or 1883
        self.client.connect(host, port)
        self.client.loop_start()

        # Wait for connection to be established (Connection Stability)
        start = time.time()
        while not self._connected and time.time() - start < 10:
            time.sleep(0.1)
        if not self._connected:
            logging.warning("Failed to establish MQTT connection within timeout")

    def disconnect(self):
        self.client.loop_stop()
        self.client.disconnect()

    def subscribe(self, topic: str):
        if topic.startswith("/uufi") and self.conn_spec.prefix:
            topic = "/" + self.conn_spec.prefix + topic
        self.client.subscribe(topic)

    def publish(self, topic: str, payload: dict):
        if topic.startswith("/uufi") and self.conn_spec.prefix:
            topic = "/" + self.conn_spec.prefix + topic
        self.client.publish(topic, json.dumps(payload), qos=1)

    def set_on_message(self, callback: Callable[[str, Any], None]):
        self.on_message_callback = callback

    def format_topic(self, sub_type: str, sub_folder: str, registry_id: str = None, device_id: str = None) -> str:
        # [/{prefix}]/uufi/[r/{registryId}/[d/{deviceId}/]]c/{subType}/{subFolder}
        parts = [""]
        if self.conn_spec.prefix:
            parts.extend(self.conn_spec.prefix.split("/"))
        parts.append("uufi")

        if registry_id:
            parts.extend(["r", registry_id])
            if device_id:
                parts.extend(["d", device_id])

        parts.extend(["c", sub_type, sub_folder])
        return "/".join(parts)

    def parse_topic(self, topic: str) -> dict:
        if not topic.startswith('/'):
            return {}
        parts = topic.split('/')
        if parts[0] == "":
            parts.pop(0)

        # Find the marker "uufi"
        try:
            uufi_idx = parts.index("uufi")
        except ValueError:
            return {}

        prefix = "/".join(parts[:uufi_idx]) if uufi_idx > 0 else None
        idx = uufi_idx + 1

        result = {}
        if prefix:
            result['prefix'] = prefix

        if idx < len(parts) and parts[idx] == "r":
            result['registryId'] = parts[idx+1]
            idx += 2
            if idx < len(parts) and parts[idx] == "d":
                result['deviceId'] = parts[idx+1]
                idx += 2

        if idx < len(parts) and parts[idx] == "c":
            result['subType'] = parts[idx+1] if len(parts) > idx + 1 else None
            result['subFolder'] = parts[idx+2] if len(parts) > idx + 2 else None

        return result

    def handshake(self):
        transaction_id = f"UUFI:handshake:{uuid.uuid4().hex[:8]}"
        topic_state = self.format_topic("state", "udmi")
        topic_config = self.format_topic("config", "udmi")

        self.subscribe(topic_config)

        handshake_complete = False
        def temp_callback(topic, payload):
            nonlocal handshake_complete
            print(f"DEBUG: handshake received {topic} {payload}")
            parsed = self.parse_topic(topic)
            if parsed.get('subType') == 'config' and parsed.get('subFolder') == 'udmi':
                # Check principal and transactionId in envelope (Matching own principal as per spec)
                p = payload.get('principal') if isinstance(payload, dict) else None
                if p == self.principal or p == self.principal.split('.')[0]:
                   unwrapped = unwrap_message(payload)
                   udmi = unwrapped.get('udmi', unwrapped)
                   if isinstance(udmi, dict) and 'reply' in udmi:
                       if udmi['reply'].get('transaction_id') == transaction_id:
                           handshake_complete = True

        old_callback = self.on_message_callback
        self.set_on_message(temp_callback)

        start = time.time()
        last_publish = 0
        while not handshake_complete and time.time() - start < 60:
            if time.time() - last_publish >= 5:
                payload = wrap_message({
                    "udmi": {
                        "setup": {
                            "functions_ver": 9,
                            "transaction_id": transaction_id,
                            "msg_source": self.principal,
                            "user": self.principal
                        }
                    }
                }, transactionId=transaction_id, principal=self.principal, source=self.principal)
                self.publish(topic_state, payload)
                last_publish = time.time()
            time.sleep(0.1)

        self.set_on_message(old_callback)
        if not handshake_complete:
            raise TimeoutError("Handshake timed out after 60 seconds")


    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self._connected = True

    def _on_message(self, client, userdata, msg):
        now = time.time()
        # Clean up old nonces (older than 5 minutes)
        self.seen_nonces = [n for n in self.seen_nonces if now - n['time'] < 300]

        try:
            payload = json.loads(msg.payload.decode('utf-8'))
        except json.JSONDecodeError:
            payload = msg.payload.decode('utf-8')

        if not self.passive and isinstance(payload, dict) and 'nonce' in payload:
            nonce = payload['nonce']
            if any(n['nonce'] == nonce for n in self.seen_nonces):
                return
            self.seen_nonces.append({'nonce': nonce, 'time': now})

        if self.on_message_callback:
            self.on_message_callback(msg.topic, payload)

class PubSubTransport:
    def __init__(self, conn_spec: ConnSpec, tag: str = None):
        self.conn_spec = conn_spec
        self.principal = conn_spec.principal
        if tag and tag != "butler":
            self.principal = f"{conn_spec.principal}.{tag}"
        # Placeholder for PubSub implementation
        pass

    def connect(self): pass
    def disconnect(self): pass
    def subscribe(self, topic: str): pass
    def publish(self, topic: str, payload: dict): pass
    def set_on_message(self, callback: Callable[[str, Any], None]): pass
    def format_topic(self, sub_type: str, sub_folder: str, registry_id: str = None, device_id: str = None) -> str:
        return f"pubsub://{self.conn_spec.host}/{sub_type}/{sub_folder}"
    def parse_topic(self, topic: str) -> dict: return {}
    def handshake(self): pass
