import json
import secrets
import datetime
import paho.mqtt.client as mqtt

class ButlerMQTTBase:
    def __init__(self, source, host="localhost", port=1883):
        self.source = source
        self.host = host
        self.port = port
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self.on_connect()
        else:
            print(f"Connection failed with code {rc}")

    def _on_message(self, client, userdata, msg):
        try:
            data = json.loads(msg.payload.decode())
            self.on_message(msg.topic, data)
        except Exception as e:
            print(f"Error decoding message: {e}")

    def on_connect(self):
        pass

    def on_message(self, topic, data):
        pass

    def connect(self):
        self.client.connect(self.host, self.port, 60)

    def loop_start(self):
        self.client.loop_start()

    def loop_forever(self):
        self.client.loop_forever()

    def generate_nonce(self):
        return secrets.token_hex(4)

    def publish(self, destination, msg_type, payload, topic=None):
        envelope = {
            "source": self.source,
            "destination": destination,
            "type": msg_type,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "nonce": self.generate_nonce(),
            "payload": payload
        }
        if topic is None:
            topic = f"butler/{destination}/{msg_type}"
        
        self.client.publish(topic, json.dumps(envelope))

    def subscribe(self, topic):
        self.client.subscribe(topic)
