import sys
import argparse
from butler.conn_spec import parse_conn_spec, get_default_conn_spec

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("conn_spec", help="Connection spec URL")
    args = parser.parse_args()

    conn_spec = parse_conn_spec(args.conn_spec)
    
    print(f"Setting up bus with conn_spec: {args.conn_spec}")

    if conn_spec.protocol == "mqtt":
        import paho.mqtt.client as mqtt
        host = conn_spec.host
        port = conn_spec.port or 1883
        print(f"Connecting to MQTT broker at {host}:{port}...")
        client = mqtt.Client()
        if conn_spec.username:
            client.username_pw_set(conn_spec.username)
        try:
            client.connect(host, port, 10)
            print("Successfully connected to MQTT broker.")
            client.disconnect()
        except Exception as e:
            print(f"Failed to connect to MQTT broker: {e}")
            sys.exit(1)
    elif conn_spec.protocol == "pubsub":
        from google.cloud import pubsub_v1
        from google.api_core import exceptions
        
        publisher = pubsub_v1.PublisherClient()
        subscriber = pubsub_v1.SubscriberClient()
        
        topic_path = publisher.topic_path(conn_spec.project_id, conn_spec.root_topic)
        sub_path = subscriber.subscription_path(conn_spec.project_id, conn_spec.subscription)
        
        print(f"Checking PubSub topic: {topic_path}")
        try:
            publisher.get_topic(topic=topic_path)
            print(f"Topic {conn_spec.root_topic} exists.")
        except exceptions.NotFound:
            print(f"Creating topic {conn_spec.root_topic}...")
            publisher.create_topic(name=topic_path)
        except Exception as e:
            print(f"Error checking topic: {e}")
            sys.exit(1)
            
        print(f"Checking PubSub subscription: {sub_path}")
        try:
            subscriber.get_subscription(subscription=sub_path)
            print(f"Subscription {conn_spec.subscription} exists.")
        except exceptions.NotFound:
            print(f"Creating subscription {conn_spec.subscription}...")
            subscriber.create_subscription(name=sub_path, topic=topic_path)
        except Exception as e:
            print(f"Error checking subscription: {e}")
            sys.exit(1)
    
    print("Bus setup complete.")

if __name__ == "__main__":
    main()
