import sys
import argparse
from butler.conn_spec import parse_conn_spec

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("conn_spec", help="Connection spec URL")
    args = parser.parse_args()

    conn_spec = parse_conn_spec(args.conn_spec)
    
    print(f"Setting up bus with conn_spec: {args.conn_spec}")

    if conn_spec.protocol == "mqtt":
        import paho.mqtt.client as mqtt
        import subprocess
        import time
        host = conn_spec.host
        port = conn_spec.port or 1883
        
        def check_mqtt():
            client = mqtt.Client()
            if conn_spec.username:
                client.username_pw_set(conn_spec.username)
            try:
                client.connect(host, port, 5)
                client.disconnect()
                return True
            except Exception:
                return False

        print(f"Checking connectivity to MQTT broker at {host}:{port}...")
        if check_mqtt():
            print("Successfully connected to MQTT broker.")
        elif host == "localhost":
            print("Failed to connect. Attempting to start local mosquitto server...")
            try:
                subprocess.Popen(["mosquitto", "-d"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                time.sleep(2)
                if check_mqtt():
                    print("Successfully started and connected to local mosquitto.")
                else:
                    print("Failed to start mosquitto or it is still not accessible.")
                    sys.exit(1)
            except Exception as e:
                print(f"Error starting mosquitto: {e}")
                sys.exit(1)
        else:
            print(f"Failed to connect to remote MQTT broker at {host}:{port}.")
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
            print(f"FAIL: Topic {conn_spec.root_topic} not found. PubSub resources must be pre-configured.")
            sys.exit(1)
        except Exception as e:
            print(f"Error checking topic: {e}")
            sys.exit(1)
            
        print(f"Checking PubSub subscription: {sub_path}")
        try:
            subscriber.get_subscription(subscription=sub_path)
            print(f"Subscription {conn_spec.subscription} exists.")
        except exceptions.NotFound:
            print(f"FAIL: Subscription {conn_spec.subscription} not found. PubSub resources must be pre-configured.")
            sys.exit(1)
        except Exception as e:
            print(f"Error checking subscription: {e}")
            sys.exit(1)
    
    print("Bus setup complete.")

if __name__ == "__main__":
    main()
