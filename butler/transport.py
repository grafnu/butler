import json
import time
import os
import paho.mqtt.client as mqtt
from butler.messaging import parse_message

class Transport:
    def connect(self): raise NotImplementedError()
    def publish(self, envelope, payload): raise NotImplementedError()
    def subscribe(self, callback): raise NotImplementedError()
    def loop_start(self): pass
    def loop_stop(self): pass

class MqttTransport(Transport):
    def __init__(self, conn_spec):
        self.conn_spec = conn_spec
        self.client = mqtt.Client()
        self.callback = None
        self.on_connect_callback = None

    def connect(self):
        host = self.conn_spec.host
        port = self.conn_spec.port or 1883
        if self.conn_spec.username:
            self.client.username_pw_set(self.conn_spec.username)
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        self.client.connect(host, port, 60)

    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            if self.on_connect_callback:
                self.on_connect_callback()
        else:
            print(f"MQTT connect failed: {rc}")

    def on_message(self, client, userdata, msg):
        if not self.callback: return
        
        # Parse topic to extract envelope
        parts = msg.topic.split('/')
        offset = 1 if self.conn_spec.prefix else 0
        
        env = {}
        data = parse_message(msg.payload)
        if not data: return
        
        payload = data.get("payload", data)
        
        # Envelope fields from JSON if present
        for field in ["transactionId", "nonce", "publishTime", "source", "projectId"]:
            if field in data: env[field] = data[field]
            
        # Envelope fields from topic
        if len(parts) >= 6 + offset and parts[1] == "uufi":
            if parts[2+offset] == "p":
                env["principal"] = parts[3+offset]
                env["subType"] = parts[4+offset]
                env["subFolder"] = parts[5+offset]
            elif parts[2+offset] == "r" and len(parts) >= 8 + offset:
                env["deviceRegistryId"] = parts[3+offset]
                env["deviceId"] = parts[5+offset]
                env["subType"] = parts[6+offset]
                env["subFolder"] = parts[7+offset]

        self.callback(env, payload, msg.topic)

    def publish(self, envelope, payload):
        topic = self.get_topic(envelope)
        
        # Prepare wrapped payload for MQTT
        # "Crucially, the top-level JSON envelope MUST NOT include fields that are already encoded in the MQTT topic structure"
        wrapped = {"payload": payload}
        for field in ["transactionId", "nonce", "publishTime", "source", "projectId"]:
            if field in envelope: wrapped[field] = envelope[field]
            
        self.client.publish(topic, json.dumps(wrapped))

    def get_topic(self, env):
        parts = ["uufi"]
        if self.conn_spec.prefix:
            parts.append(self.conn_spec.prefix)
            
        if env.get("deviceRegistryId"):
            parts.extend(["r", env["deviceRegistryId"], "d", env["deviceId"], env["subType"], env["subFolder"]])
        else:
            principal = env.get("principal") or self.conn_spec.username or "system"
            parts.extend(["p", principal, env["subType"], env["subFolder"]])
            
        return "/" + "/".join(parts)

    def subscribe(self, topic, callback):
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

    def connect(self):
        pass # PubSub is serverless

    def publish(self, envelope, payload):
        attributes = {}
        for k, v in envelope.items():
            if k != "payload" and v is not None:
                attributes[k] = str(v)
        
        # In PubSub, the principal attribute might need special handling
        if self.conn_spec.principal and "principal" not in attributes:
            attributes["principal"] = self.conn_spec.principal

        data = json.dumps(payload).encode("utf-8")
        self.publisher.publish(self.topic_path, data, **attributes)

    def subscribe(self, callback):
        self.callback = callback
        
        def wrapped_callback(message):
            env = dict(message.attributes)
            payload = parse_message(message.data)
            
            # Filtering: Only include messages that have matching principal or attribute missing
            msg_principal = env.get("principal")
            if msg_principal and self.conn_spec.principal and msg_principal != self.conn_spec.principal:
                message.nack() # Should probably ack if we just want to ignore it
                return
            
            self.callback(env, payload, self.subscription_path)
            message.ack()

        self.streaming_pull_future = self.subscriber.subscribe(self.subscription_path, callback=wrapped_callback)

    def loop_stop(self):
        if hasattr(self, 'streaming_pull_future'):
            self.streaming_pull_future.cancel()

def get_transport(conn_spec):
    if conn_spec.protocol == "pubsub":
        return PubSubTransport(conn_spec)
    return MqttTransport(conn_spec)
