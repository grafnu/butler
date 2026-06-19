import sys
import argparse
import json
from butler.conn_spec import parse_conn_spec
from butler.transport import get_transport

def on_message(env, payload, topic, raw):
    # Output the raw payload as received on the bus to ensure compliance with the spec.
    # UUFI Section 9.2: single line, no truncation.
    single_line_raw = raw.replace('\n', ' ').replace('\r', '')
    print(f"{topic}: {single_line_raw}", flush=True)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("pos_conn_spec", nargs="?", help="Connection spec URL")
    parser.add_argument("--conn_spec", help="Connection spec URL")
    args, unknown = parser.parse_known_args()

    conn_str = args.conn_spec or args.pos_conn_spec
    conn_spec = parse_conn_spec(conn_str, differentiator="observe", is_passive=True)
    sys.stderr.write(f"{conn_spec.format_conn_spec()}\n")
    
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
