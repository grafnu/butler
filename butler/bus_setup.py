import sys
import argparse
import paho.mqtt.client as mqtt
from butler.conn_spec import parse_conn_spec

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("conn_spec", help="Connection spec URL")
    args = parser.parse_args()

    conn_spec = parse_conn_spec(args.conn_spec)
    host = conn_spec.host
    port = conn_spec.port or 1883
    
    print(f"Setting up bus with conn_spec: {args.conn_spec}")
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
    
    print("Bus setup complete.")

if __name__ == "__main__":
    main()
