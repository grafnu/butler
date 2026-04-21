import json
import time
import uuid
from datetime import datetime
import paho.mqtt.client as mqtt

class ButlerMessage:
    def __init__(self, source, destination, msg_type, payload):
        self.source = source
        self.destination = destination
        self.msg_type = msg_type
        self.timestamp = datetime.utcnow().isoformat() + "Z"
        self.nonce = str(uuid.uuid4())
        self.payload = payload

    def to_json(self):
        return json.dumps({
            "source": self.source,
            "destination": self.destination,
            "type": self.msg_type,
            "timestamp": self.timestamp,
            "nonce": self.nonce,
            "payload": self.payload
        })

    @staticmethod
    def from_json(json_str):
        data = json.loads(json_str)
        return data

class ButlerMQTTClient:
    def __init__(self, client_id, host="localhost", port=1883):
        self.client_id = client_id
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id)
        self.host = host
        self.port = port
        self.on_message_callback = None
        self.subscriptions = set()

    def connect(self):
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        print(f"[{self.client_id}] Connecting to {self.host}:{self.port}...")
        self.client.connect(self.host, self.port, 60)
        self.client.loop_start()

    def disconnect(self):
        self.client.loop_stop()
        self.client.disconnect()

    def subscribe(self, topic):
        self.subscriptions.add(topic)
        if self.client.is_connected():
            print(f"[{self.client_id}] Subscribing to {topic}")
            self.client.subscribe(topic)

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        if rc == 0:
            print(f"[{self.client_id}] Connected to MQTT broker.")
            for topic in self.subscriptions:
                print(f"[{self.client_id}] Subscribing to {topic}")
                self.client.subscribe(topic)
        else:
            print(f"[{self.client_id}] Failed to connect to MQTT broker, return code {rc}")

    def publish(self, topic, destination, msg_type, payload):
        msg = ButlerMessage(self.client_id, destination, msg_type, payload)
        self.client.publish(topic, msg.to_json())

    def _on_message(self, client, userdata, msg):
        try:
            data = ButlerMessage.from_json(msg.payload.decode())
            if self.on_message_callback:
                self.on_message_callback(msg.topic, data)
        except Exception as e:
            print(f"[{self.client_id}] Error processing message on {msg.topic}: {e}")

    def set_on_message(self, callback):
        self.on_message_callback = callback
