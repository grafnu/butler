import sys
import time
import argparse
from butler.transport import parse_conn_spec, MqttTransport, wrap_message, unwrap_message
from butler.model_repo import ModelRepo
import urllib.request
import hashlib

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

    if len(args) < 2:
        print("Usage: bin/mocket [conn_spec] registry_id device_id")
        sys.exit(1)

    conn_spec = parse_conn_spec(conn_spec_str)
    print(f"Conn spec: scheme={conn_spec.scheme}, host={conn_spec.host}, port={conn_spec.port}, principal={conn_spec.principal}, prefix={conn_spec.prefix}")

    transport = MqttTransport(conn_spec, tag="mocket")
    model_repo = ModelRepo()
    registry_id = args[0]
    device_id = args[1]

    state = "quiescent"
    current_version = None
    lkg_version = None
    subsystem = "main"

    def on_message(topic, payload):
        nonlocal state, current_version, subsystem
        parsed = transport.parse_topic(topic)
        subType = parsed.get('subType')
        subFolder = parsed.get('subFolder')
        unwrapped = unwrap_message(payload)

        if subType == 'config' and subFolder == 'blobset' and parsed.get('deviceId') == device_id:
            blobset_wrap = unwrapped.get('blobset', {})
            # Handle both nested (with 'blobs') and unnested
            blobs = blobset_wrap.get('blobs', blobset_wrap)
            for sub_name, sub_update in blobs.items():
                if not isinstance(sub_update, dict) or sub_name in ['version', 'timestamp']: continue
                subsystem = sub_name
                if 'url' in sub_update and 'sha256' in sub_update:
                    state = "pending"
                    publish_status()
                    time.sleep(2)
                    state = "success"
                    current_version = sub_update.get('version')
                    publish_status()

    def publish_status():
        topic = transport.format_topic("state", "blobset", registry_id, device_id)
        # Spec 8.1: include 'blobs' wrapper and mandatory make/model
        msg = wrap_message({"blobset": {"blobs": {subsystem: {"status": state, "current_version": current_version, "lkg_version": lkg_version, "make": "default", "model": "default"}}}}, principal=transport.principal)
        transport.publish(topic, msg)

    transport.set_on_message(on_message)
    transport.connect()
    transport.subscribe("/uufi/#")

    try:
        while True:
            publish_status()
            time.sleep(5)
    except KeyboardInterrupt: pass
    finally: transport.disconnect()

if __name__ == '__main__':
    main()
