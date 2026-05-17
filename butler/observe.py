import sys
import json
import argparse
from butler.transport import parse_conn_spec, MqttTransport

def on_message(topic, payload):
    if isinstance(payload, dict):
        payload_str = json.dumps(payload)
    else:
        payload_str = str(payload)
    print(f"{topic}: {payload_str}", flush=True)

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
    print(f"Conn spec: scheme={conn_spec.scheme}, host={conn_spec.host}, port={conn_spec.port}, principal={conn_spec.principal}, prefix={conn_spec.prefix}")

    transport = MqttTransport(conn_spec, tag="observe", passive=True)
    transport.set_on_message(on_message)
    transport.connect()
    transport.subscribe("#")

    try:
        import time
        while True:
            time.sleep(1)
    except KeyboardInterrupt: pass
    finally: transport.disconnect()

if __name__ == '__main__':
    main()
