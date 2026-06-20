import sys
import argparse
import os
from butler.model_repo import ModelRepository
from butler.blob_repo import BlobRepository
from butler.conn_spec import parse_conn_spec, split_device_id, get_default_registry_id
from butler.transport import get_transport
from butler.messaging import create_envelope, create_payload

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("pos_args", nargs="*", help="[registry_id] <device_id> <subsystem_id> <version> <blob_path>")
    parser.add_argument("--conn_spec", help="Connection spec URL (overrides default)")
    args, unknown = parser.parse_known_args()

    pos_args = args.pos_args
    conn_spec_str = args.conn_spec
    if not conn_spec_str and pos_args and ("://" in pos_args[0]):
        conn_spec_str = pos_args.pop(0)

    if not pos_args or len(pos_args) < 4:
        print("Error: device_id, subsystem_id, version, and blob_path are required", file=sys.stderr)
        sys.exit(1)

    blob_path = pos_args[-1]
    version = pos_args[-2]
    subsystem = pos_args[-3]
    rem = pos_args[:-3]
    
    repo = ModelRepository()
    
    registry_id = get_default_registry_id()
    device_id = None

    if len(pos_args) == 4:
        # device_id, subsystem_id, version, blob_path
        registry_id, device_id = split_device_id(pos_args[0])
        subsystem = pos_args[1]
        version = pos_args[2]
        blob_path = pos_args[3]
    elif len(pos_args) == 5:
        # registry_id, device_id, subsystem_id, version, blob_path
        registry_id = pos_args[0]
        device_id = pos_args[1]
        subsystem = pos_args[2]
        version = pos_args[3]
        blob_path = pos_args[4]
    else:
        print("Error: Too many arguments. Usage: trigger [registry_id] <device_id> <subsystem_id> <version> <blob_path>", file=sys.stderr)
        sys.exit(1)

    conn_spec = parse_conn_spec(conn_spec_str, differentiator="cli")
    sys.stderr.write(f"{conn_spec.format_conn_spec()}\n")
    
    state = repo.get_device_state(registry_id, device_id, subsystem)
    if not state:
        # Fallback: check any subsystem for this device to get make/model
        registries = repo.get_all_registries()
        devices = registries.get(registry_id, {}).get("devices", {})
        dev_subsystems = devices.get(device_id, {})
        if dev_subsystems:
            # Use first available subsystem to get metadata
            first_sub = list(dev_subsystems.values())[0]
            state = first_sub
        else:
            print(f"Device {device_id} in registry {registry_id} not found in model.")
            sys.exit(1)
        
    if not os.path.exists(blob_path):
        print(f"Blob path {blob_path} not found.")
        sys.exit(1)

    with open(blob_path, "rb") as f:
        BlobRepository().store_blob(state.get("make", "unknown"), state.get("model", "unknown"), subsystem, version, f.read())
    
    repo.set_target_version(registry_id, device_id, subsystem, version)

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

    # Construct the partial merge update payload (Section 12.6)
    update_data = {
        "operation": "UPDATE",
        "registries": {
            registry_id: {
                "devices": {
                    device_id: {
                        "system": {
                            "software": {
                                subsystem: version
                            },
                            "make": state.get("make", "unknown"),
                            "model": state.get("model", "unknown")
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
