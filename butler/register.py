import sys
import argparse
from butler.model_repo import ModelRepository
from butler.conn_spec import parse_conn_spec, split_device_id, get_default_registry_id
from butler.transport import get_transport

from butler.messaging import create_envelope, create_payload

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("pos_args", nargs="*", help="[registry_id] <device_id> [make] [model]")
    parser.add_argument("--conn_spec", help="Connection spec URL (overrides default)")
    args, unknown = parser.parse_known_args()

    pos_args = args.pos_args
    conn_spec_str = args.conn_spec
    if not conn_spec_str and pos_args and ("://" in pos_args[0]):
        conn_spec_str = pos_args.pop(0)

    if not pos_args:
        print("Error: device_id is required", file=sys.stderr)
        sys.exit(1)

    # Logic to handle [registry_id] <device_id> [make] [model]
    # If 1 arg: device_id (which could be reg/dev)
    # If 2 args: registry_id, device_id
    # If 3 args: registry_id, device_id, make
    # If 4 args: registry_id, device_id, make, model
    
    if len(pos_args) == 1:
        registry_id, device_id = split_device_id(pos_args[0])
        make = "unknown"
        model = "unknown"
    elif len(pos_args) == 2:
        registry_id = pos_args[0]
        device_id = pos_args[1]
        make = "unknown"
        model = "unknown"
    elif len(pos_args) == 3:
        registry_id = pos_args[0]
        device_id = pos_args[1]
        make = pos_args[2]
        model = "unknown"
    else:
        registry_id = pos_args[0]
        device_id = pos_args[1]
        make = pos_args[2]
        model = pos_args[3]

    conn_spec = parse_conn_spec(conn_spec_str, differentiator="cli")
    sys.stderr.write(f"{conn_spec.format_conn_spec()}\n")
    repo = ModelRepository()
    repo.set_device_info(registry_id, device_id, "main", make, model)

    # Publish the updated model to MQTT
    transport = get_transport(conn_spec)
    transport.connect()
    env = create_envelope(
        registry_id=registry_id,
        device_id=device_id,
        sub_type="model",
        sub_folder="cloud",
        source=conn_spec.source_id
    )
    
    # Construct the partial merge update payload (Butler Section 8)
    update_data = {
        "operation": "UPDATE",
        "registries": {
            registry_id: {
                "devices": {
                    device_id: {
                        "main": {
                            "make": make,
                            "model": model
                        }
                    }
                }
            }
        }
    }
    
    payload = create_payload("cloud", update_data)
    transport.loop_start()
    info = transport.publish(env, payload)
    info.wait()
    transport.loop_stop()

if __name__ == "__main__":
    main()
