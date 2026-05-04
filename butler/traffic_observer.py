import sys
import argparse
import json
from butler.conn_spec import parse_conn_spec, get_default_conn_spec
from butler.transport import get_transport

def on_message(env, payload, topic):
    # Reconstruct the wrapped message for display if it's not already raw
    # For MQTT, the spec says "{topic}: {payload}" where {payload} is the complete JSON-encoded message.
    # Our transport extracts 'env' and 'payload'. Let's reconstruct it.
    
    # Actually, let's just create a combined object that looks like what's on the bus.
    wrapped = {"payload": payload}
    for field in ["transactionId", "nonce", "publishTime", "source", "projectId", "principal", "deviceId", "deviceRegistryId", "subType", "subFolder"]:
        if env.get(field):
            wrapped[field] = env[field]
    
    print(f"{topic}: {json.dumps(wrapped)}", flush=True)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("conn_spec", help="Connection spec URL")
    args = parser.parse_args()

    conn_spec = parse_conn_spec(args.conn_spec)
    # print(f"Observer starting with {args.conn_spec}...", flush=True)
    
    transport = get_transport(conn_spec)
    transport.connect()
    
    if conn_spec.protocol == "mqtt":
        transport.subscribe("/uufi/#" if not conn_spec.prefix else f"/uufi/{conn_spec.prefix}/#", on_message)
    else:
        transport.subscribe(on_message)

    try:
        while True:
            import time
            time.sleep(1)
    except KeyboardInterrupt:
        transport.loop_stop()

if __name__ == "__main__":
    main()
