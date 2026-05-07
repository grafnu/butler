import sys
import argparse
from butler.model_repo import ModelRepository
from butler.conn_spec import parse_conn_spec
from butler.transport import get_transport
from butler.messaging import create_envelope, create_payload

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("registry_id", help="Registry ID")
    parser.add_argument("device_id", help="Device ID")
    args = parser.parse_args()

    print(f"Registering device {args.device_id} in registry {args.registry_id}")
    repo = ModelRepository()
    repo.set_device_info(args.registry_id, args.device_id, "main", "vibrant", "butler-v1")

    # Publish the updated model to MQTT
    conn_spec = parse_conn_spec(None, differentiator="cli")
    transport = get_transport(conn_spec)
    transport.connect()

    env = create_envelope(
        sub_type="config",
        sub_folder="cloud",
        source="cli"
    )
    payload = create_payload("cloud", repo.data)
    transport.publish(env, payload)
    transport.loop_stop()

if __name__ == "__main__":
    main()
