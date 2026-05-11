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
    @property
    def is_connected(self): return True

class MqttTransport(Transport):
    def __init__(self, conn_spec):
        self.conn_spec = conn_spec
        self.client = mqtt.Client()
        self.callback = None
        self.on_connect_callback = None
        self._is_connected = False

    @property
    def is_connected(self):
        return self._is_connected

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
            self._is_connected = True
            if self.on_connect_callback:
                self.on_connect_callback()
        else:
            self._is_connected = False
            print(f"MQTT connect failed: {rc}")

    def on_message(self, client, userdata, msg):
        if not self.callback: return
        
        raw_payload = msg.payload.decode('utf-8', errors='replace')

        data = parse_message(msg.payload)
        
        env = {}
        payload = None
        if data:
            if "payload" not in data:
                return  # Reject message lacking nested 'payload' key
            payload = data.get("payload")
            # Envelope fields from JSON if present
            for field in ["transactionId", "nonce", "publishTime", "source", "projectId", "principal"]:
                if field in data: env[field] = data[field]
        else:
            return  # Reject non-JSON or missing payload
        
        # Parse topic to extract envelope
        # Structure: /{prefix}/uufi/[r/{registryId}/[d/{deviceId}/]]c/{subType}/{subFolder}
        parts = msg.topic.strip('/').split('/')
        
        try:
            uufi_idx = parts.index("uufi")
        except ValueError:
            return

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

        # Reject redundant envelope fields per spec 9.3
        for field in ["deviceRegistryId", "deviceId", "subType", "subFolder"]:
            if field in data and field in topic_env:
                return  # Reject message containing redundant envelope fields

        env.update(topic_env)

        self.callback(env, payload, msg.topic, raw_payload)

    def publish(self, envelope, payload):
        topic = self.get_topic(envelope)

        
        # Prepare wrapped payload for MQTT
        # "Crucially, the top-level JSON envelope MUST only include data NOT already encoded in the MQTT topic structure"
        wrapped = {"payload": payload}
        for field in ["transactionId", "nonce", "publishTime", "source", "projectId", "principal"]:
            if field in envelope: wrapped[field] = envelope[field]
        
        if "principal" not in wrapped and self.conn_spec.principal:
            wrapped["principal"] = self.conn_spec.principal
            
        self.client.publish(topic, json.dumps(wrapped))

    def get_topic(self, env):
        parts = []
        if self.conn_spec.prefix:
            parts.append(self.conn_spec.prefix)
        parts.append("uufi")
            
        if env.get("deviceRegistryId"):
            parts.extend(["r", env["deviceRegistryId"]])
            if env.get("deviceId"):
                parts.extend(["d", env["deviceId"]])
        
        parts.append("c")
        parts.extend([env.get("subType", "unknown"), env.get("subFolder", "unknown")])
            
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
