import sys
import argparse
import json
from butler.conn_spec import parse_conn_spec
from butler.transport import get_transport

def on_message(env, payload, topic, raw):
    # Output the raw payload as received on the bus to ensure compliance with the spec.
    print(f"{topic}: {raw}", flush=True)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("conn_spec", nargs="?", help="Connection spec URL")
    args = parser.parse_args()

    conn_spec = parse_conn_spec(args.conn_spec, differentiator="observe")
    print(f"Starting Observer with {conn_spec}...", flush=True)

    transport = get_transport(conn_spec)
    transport.connect()

    if conn_spec.protocol == "mqtt":
        prefix = conn_spec.prefix + '/' if conn_spec.prefix else ''
        transport.subscribe(f"/{prefix}uufi/#", on_message)
    else:
        transport.subscribe(on_message)

    transport.loop_start()

    try:
        while True:
            import time
            time.sleep(1)
    except KeyboardInterrupt:
        transport.loop_stop()

if __name__ == "__main__":
    main()
