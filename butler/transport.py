import re
import urllib.parse
from dataclasses import dataclass
from typing import Optional, Callable, Any, List, Dict
import json
import paho.mqtt.client as mqtt
import logging
import time
import uuid
from datetime import datetime, timezone

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

    # Redundancy Rule (9.3): Top-level envelope fields MUST only include data NOT already encoded in the MQTT topic.
    # These are typically: deviceRegistryId, deviceId, subType, subFolder
    for redundant in ['deviceRegistryId', 'deviceId', 'subType', 'subFolder']:
        if redundant in msg:
            del msg[redundant]

    now = get_timestamp()

    if 'publishTime' not in msg:
        msg['publishTime'] = now
    if 'nonce' not in msg:
        msg['nonce'] = uuid.uuid4().hex[:8]
    if 'principal' not in msg and 'source' in msg:
        msg['principal'] = msg['source']

    # Mandatory Payload Structure (9.1): UDMI message data MUST NOT be at top level,
    # MUST be strictly nested within the 'payload' key.
    if 'payload' not in msg:
        msg['payload'] = payload.copy()

    if 'timestamp' not in msg['payload']:
        msg['payload']['timestamp'] = now
    if 'version' not in msg['payload']:
        msg['payload']['version'] = "1.5.2"

    return msg

def unwrap_message(msg: dict, topic_parts: dict = None) -> dict:
    """Unwraps message and enforces compliance."""
    if not isinstance(msg, dict) or 'payload' not in msg:
        raise ValueError("Invalid message structure: Missing mandatory 'payload' wrapper.")

    # Redundancy Check (9.3): Reject messages with redundant envelope fields already present in topic.
    if topic_parts:
        for field in ['deviceRegistryId', 'deviceId', 'subType', 'subFolder']:
            if field in msg and field in topic_parts and msg[field] == topic_parts[field]:
                raise ValueError(f"Redundancy violation: Envelope field '{field}' is already present in topic.")

    return msg['payload']

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
        self.client.subscribe(topic)

    def publish(self, topic: str, payload: dict):
        self.client.publish(topic, json.dumps(payload), qos=1)

    def set_on_message(self, callback: Callable[[str, Any], None]):
        self.on_message_callback = callback

    def format_topic(self, sub_type: str, sub_folder: str, registry_id: str = None, device_id: str = None) -> str:
        # /uufi/[r/{registryId}/[d/{deviceId}/]]c/{subType}/{subFolder}
        parts = ["", "uufi"]
        if self.conn_spec.prefix:
            parts.append(self.conn_spec.prefix)

        if registry_id:
            parts.extend(["r", registry_id])
            if device_id:
                parts.extend(["d", device_id])

        parts.extend(["c", sub_type, sub_folder])
        return "/".join(parts)

    def parse_topic(self, topic: str) -> dict:
        parts = topic.split('/')
        if parts[0] == "":
            parts.pop(0)

        if len(parts) < 3 or parts[0] != "uufi":
            return {}

        idx = 1
        prefix = None
        if parts[idx] not in ["r", "c"]: # Handling prefix if present
             prefix = parts[idx]
             idx += 1

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
            parsed = self.parse_topic(topic)
            if parsed.get('subType') == 'config' and parsed.get('subFolder') == 'udmi':
                # Check principal and transactionId in envelope (Matching own principal as per spec)
                if payload.get('principal') == self.principal:
                   try:
                       unwrapped = unwrap_message(payload, topic_parts=parsed)
                   except ValueError as e:
                       logging.error(f"Handshake error: {e}")
                       return

                   if 'udmi' in unwrapped and 'reply' in unwrapped['udmi']:
                       if unwrapped['udmi']['reply'].get('transaction_id') == transaction_id:
                           handshake_complete = True

        old_callback = self.on_message_callback
        self.set_on_message(temp_callback)

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

        start = time.time()
        while not handshake_complete and time.time() - start < 60:
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
