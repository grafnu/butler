import sys
import argparse
import os
from butler.model_repo import ModelRepository
from butler.blob_repo import BlobRepository
from butler.conn_spec import parse_conn_spec
from butler.transport import get_transport
from butler.messaging import create_envelope, create_payload

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("registry_id", help="Registry ID")
    parser.add_argument("device_id", help="Device ID")
    parser.add_argument("blob_version", help="Blob Version")
    parser.add_argument("blob_path", help="Blob Path")
    args = parser.parse_args()

    print(f"Triggering update for {args.device_id} in {args.registry_id} to version {args.blob_version}")

    repo = ModelRepository()
    state = repo.get_device_state(args.registry_id, args.device_id)
    if not state:
        print(f"Device {args.device_id} in registry {args.registry_id} not found in model.")
        sys.exit(1)

    if not os.path.exists(args.blob_path):
        print(f"Blob path {args.blob_path} not found.")
        sys.exit(1)

    with open(args.blob_path, "rb") as f:
        BlobRepository().store_blob(state["make"], state["model"], "main", args.blob_version, f.read())

    repo.set_target_version(args.registry_id, args.device_id, args.blob_version)

    # Publish the updated model to MQTT
    conn_spec = parse_conn_spec(None, differentiator="cli")
    transport = get_transport(conn_spec)
    transport.connect()

    env = create_envelope(
        sub_type="model",
        sub_folder="cloud",
        source="cli"
    )

    # Construct the partial merge update payload
    update_data = {
        "operation": "UPDATE",
        "registries": {
            args.registry_id: {
                "devices": {
                    args.device_id: {
                        "target_version": args.blob_version
                    }
                }
            }
        }
    }

    payload = create_payload("cloud", update_data)
    transport.publish(env, payload)
    transport.loop_stop()

if __name__ == "__main__":
    main()
