import re
import urllib.parse
from dataclasses import dataclass
from typing import Optional

@dataclass
class ConnSpec:
    scheme: str
    host: str
    port: Optional[int]
    principal: Optional[str]
    prefix: Optional[str]

def parse_conn_spec(conn_spec_str: str) -> ConnSpec:
    parsed = urllib.parse.urlparse(conn_spec_str)

    scheme = parsed.scheme
    if scheme not in ["mqtt", "pubsub"]:
        raise ValueError(f"Unsupported scheme: {scheme}")

    principal = parsed.username
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

import json
import paho.mqtt.client as mqtt
import logging
from typing import Callable, Any

def wrap_message(payload: dict, **envelope_kwargs) -> dict:
    from datetime import datetime, timezone
    import uuid

    msg = envelope_kwargs.copy()

    # Omit fields already encoded in the MQTT topic structure
    for key in ['deviceId', 'registryId', 'subFolder', 'subType', 'projectId']:
        msg.pop(key, None)

    now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    if 'publishTime' not in msg:
        msg['publishTime'] = now
    if 'nonce' not in msg:
        msg['nonce'] = uuid.uuid4().hex[:8]

    if 'payload' not in msg:
        msg['payload'] = payload.copy()

    if 'timestamp' not in msg['payload']:
        msg['payload']['timestamp'] = now
    if 'version' not in msg['payload']:
        msg['payload']['version'] = "1.5.2"

    return msg

def unwrap_message(msg: dict) -> dict:
    return msg.get('payload', msg)

class MqttTransport:
    def __init__(self, conn_spec: ConnSpec):
        self.conn_spec = conn_spec
        self.client = mqtt.Client()
        self.on_message_callback: Optional[Callable[[str, Any], None]] = None
        self.seen_nonces = []

        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message

    def connect(self):
        host = self.conn_spec.host
        port = self.conn_spec.port or 1883
        self.client.connect(host, port)
        self.client.loop_start()

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
        parts = ["", "uufi"]
        if self.conn_spec.prefix:
            parts.append(self.conn_spec.prefix)

        if registry_id and device_id:
            parts.extend(["r", registry_id, "d", device_id])
        elif self.conn_spec.principal:
            parts.extend(["p", self.conn_spec.principal])
        else:
            raise ValueError("Must provide either registry_id/device_id or have a principal configured")

        parts.extend([sub_type, sub_folder])
        return "/".join(parts)

    def parse_topic(self, topic: str) -> dict:
        parts = topic.split('/')
        if parts[0] == "":
            parts.pop(0)

        if len(parts) < 4 or parts[0] != "uufi":
            return {}

        idx = 1
        prefix = None
        if parts[idx] not in ["r", "p"]:
            prefix = parts[idx]
            idx += 1

        result = {}
        if prefix:
            result['prefix'] = prefix

        if parts[idx] == "r":
            result['registryId'] = parts[idx+1]
            if parts[idx+2] == "d":
                result['deviceId'] = parts[idx+3]
                result['subType'] = parts[idx+4]
                result['subFolder'] = parts[idx+5]
        elif parts[idx] == "p":
            result['principal'] = parts[idx+1]
            result['subType'] = parts[idx+2]
            result['subFolder'] = parts[idx+3]

        return result

    def handshake(self):
        import time
        import uuid

        transaction_id = str(uuid.uuid4())
        topic_state = self.format_topic("state", "udmi")
        topic_config = self.format_topic("config", "udmi")

        self.subscribe(topic_config)

        handshake_complete = False
        def temp_callback(topic, payload):
            nonlocal handshake_complete
            if topic == topic_config:
                unwrapped = unwrap_message(payload)
                if 'udmi' in unwrapped and 'reply' in unwrapped['udmi']:
                    if unwrapped['udmi']['reply'].get('transaction_id') == transaction_id:
                        handshake_complete = True

        old_callback = self.on_message_callback
        self.set_on_message(temp_callback)

        payload = wrap_message({
            "udmi": {
                "setup": {
                    "functions_ver": 9,
                    "transaction_id": transaction_id
                }
            }
        })
        self.publish(topic_state, payload)

        start = time.time()
        while not handshake_complete and time.time() - start < 30:
            time.sleep(0.1)

        self.set_on_message(old_callback)
        if not handshake_complete:
            raise TimeoutError("Handshake timed out")

    def _on_connect(self, client, userdata, flags, rc):
        pass

    def _on_message(self, client, userdata, msg):
        if self.on_message_callback:
            try:
                payload = json.loads(msg.payload.decode('utf-8'))
            except json.JSONDecodeError:
                payload = msg.payload.decode('utf-8')

            if isinstance(payload, dict) and 'nonce' in payload:
                nonce = payload['nonce']
                if nonce in self.seen_nonces:
                    return
                self.seen_nonces.append(nonce)
                if len(self.seen_nonces) > 1000:
                    self.seen_nonces.pop(0)

            self.on_message_callback(msg.topic, payload)
