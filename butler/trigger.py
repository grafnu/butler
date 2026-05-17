import sys
import argparse
from butler.model_repo import ModelRepo
from butler.blob_repo import BlobRepo
from butler.transport import parse_conn_spec

def main():

    parser = argparse.ArgumentParser()
    parser.add_argument("--conn_spec", help="Connection spec")
    parser.add_argument("args", nargs="*", help="Arguments")
    args_obj, unknown = parser.parse_known_args()
    
    args = args_obj.args
    conn_spec_str = args_obj.conn_spec
    if not conn_spec_str and args and ("://" in args[0] or args[0].startswith("localhost")):
        conn_spec_str = args.pop(0)
    
    if not conn_spec_str:
        from butler.transport import get_default_conn_spec
        conn_spec_str = get_default_conn_spec()
    
    conn_spec = parse_conn_spec(conn_spec_str)
    from butler.transport import MqttTransport, wrap_message
    import time

    if len(args) < 5:
        print("Usage: bin/trigger [conn_spec] [registry_id] <device_id> <subsystem_id> <version> <blob_path>")
        sys.exit(1)

    registry_id = args[0]
    device_id = args[1]
    subsystem = args[2]
    blob_version = args[3]
    blob_path = args[4]

    model_repo = ModelRepo()
    model = model_repo.get_model()
    reg = model.get("registries", {}).get(registry_id, {})
    device = reg.get("devices", {}).get(device_id)

    if not device:
        print(f"Device {device_id} not found in registry {registry_id} in model. Register it first.")
        sys.exit(1)

    make = device.get(subsystem, {}).get("make") or device.get("main", {}).get("make", "unknown")
    model_name = device.get(subsystem, {}).get("model") or device.get("main", {}).get("model", "unknown")

    blob_repo = BlobRepo()
    hash_hex = blob_repo.store_blob(make, model_name, subsystem, blob_version, blob_path)
    print(f"Stored blob with hash {hash_hex}.")

    model_repo.update_target_version(registry_id, device_id, subsystem, blob_version)
    
    # Section 11.3: Tools that modify the model MUST also publish a model/cloud message
    transport = MqttTransport(conn_spec, tag="trigger")
    transport.connect()
    topic = transport.format_topic("model", "cloud")
    msg = wrap_message({
        "cloud": {
            "operation": "UPDATE",
            "registries": {
                registry_id: {
                    "devices": {
                        device_id: {
                            subsystem: {
                                "target_version": blob_version
                            }
                        }
                    }
                }
            }
        }
    }, principal=transport.principal, source=transport.principal)
    transport.publish(topic, msg)
    time.sleep(1)
    transport.disconnect()
    
    print(f"Updated target_version to {blob_version} for {device_id} in {registry_id} subsystem {subsystem} and published cloud update.")

if __name__ == '__main__':
    main()
