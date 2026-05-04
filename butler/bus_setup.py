import os
import argparse
import sys
from butler.model_repo import ModelRepository
from butler.blob_repo import BlobRepository
from butler.common import parse_conn_spec, get_default_conn_spec
import subprocess
import time
import socket

def check_mqtt_connectivity(host, port):
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except (socket.timeout, ConnectionRefusedError):
        return False

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("conn_spec", nargs="?", help="Connection specification")
    args = parser.parse_args()

    conn_spec = args.conn_spec or get_default_conn_spec()
    scheme, user, host, port_or_topic, prefix = parse_conn_spec(conn_spec)

    print(f"Setting up Butler environment with {conn_spec}...")
    project_id = os.environ.get("BUTLER_PROJECT_ID", "vibrant")
    registry_id = os.environ.get("BUTLER_REGISTRY_ID", "controller")
    
    print(f"Project ID: {project_id}")
    print(f"Registry ID: {registry_id}")
    print(f"Scheme: {scheme}")
    print(f"Host: {host}")
    
    if scheme == "pubsub":
        print(f"Topic: {port_or_topic}")
        print("Checking PubSub connectivity (dry-run)...")
        # For PubSub, we just assume it's setup as per AGENTS.md, 
        # but we can check if the library is available.
        try:
            from google.cloud import pubsub_v1
            print("PubSub library available.")
        except ImportError:
            print("Error: google-cloud-pubsub not installed.")
            sys.exit(1)
    else:
        port = port_or_topic
        print(f"Port: {port}")
        if host == "localhost":
            if not check_mqtt_connectivity(host, port):
                print(f"MQTT broker not running on {host}:{port}. Attempting to start mosquitto...")
                try:
                    subprocess.run(["mosquitto", "-d"], check=True)
                    time.sleep(1)
                    if check_mqtt_connectivity(host, port):
                        print("Successfully started mosquitto.")
                    else:
                        print("Failed to start mosquitto or it is taking too long.")
                except Exception as e:
                    print(f"Error starting mosquitto: {e}")
            else:
                print(f"MQTT broker is already running on {host}:{port}.")
        else:
            if not check_mqtt_connectivity(host, port):
                print(f"Error: Cannot connect to MQTT broker at {host}:{port}")
                sys.exit(1)
            print(f"Successfully connected to MQTT broker at {host}:{port}")

    if prefix:
        print(f"Topic Prefix: {prefix}")
    
    model_repo = ModelRepository()
    # Clean up model for a fresh start
    model_repo.save_model({})
    
    blob_repo = BlobRepository()
    
    # Initialize some defaults if needed
    if not os.path.exists("testing"):
        os.makedirs("testing")
    
    print("Done.")

if __name__ == "__main__":
    main()
