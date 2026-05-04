import os
import argparse
from butler.model_repo import ModelRepository
from butler.blob_repo import BlobRepository
from butler.common import parse_conn_spec

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("conn_spec", nargs="?", help="Connection specification")
    args = parser.parse_args()

    user, host, port, prefix = parse_conn_spec(args.conn_spec)

    print("Setting up Butler environment...")
    project_id = os.environ.get("BUTLER_PROJECT_ID", "vibrant")
    registry_id = os.environ.get("BUTLER_REGISTRY_ID", "controller")
    
    print(f"Project ID: {project_id}")
    print(f"Registry ID: {registry_id}")
    print(f"MQTT Host: {host}")
    print(f"MQTT Port: {port}")
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
