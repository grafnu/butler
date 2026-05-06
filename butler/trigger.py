import sys
import argparse
import os
from butler.model_repo import ModelRepository
from butler.blob_repo import BlobRepository
from butler.conn_spec import parse_conn_spec

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("device_id")
    parser.add_argument("blob_version")
    parser.add_argument("blob_path")
    args = parser.parse_args()

    print(f"Triggering update for {args.device_id} to version {args.blob_version}")
    
    repo = ModelRepository()
    state = repo.get_device_state(args.device_id, "main")
    if not state:
        print(f"Device {args.device_id} not found in model.")
        sys.exit(1)
        
    if not os.path.exists(args.blob_path):
        print(f"Blob path {args.blob_path} not found.")
        sys.exit(1)

    with open(args.blob_path, "rb") as f:
        BlobRepository().store_blob(state["make"], state["model"], "main", args.blob_version, f.read())
    
    repo.set_target_version(args.device_id, "main", args.blob_version)

if __name__ == "__main__":
    main()
