import sys
import json
from butler.transport import parse_conn_spec, MqttTransport

def on_message(topic, payload):
    if isinstance(payload, dict):
        payload_str = json.dumps(payload)
    else:
        payload_str = str(payload)
    print(f"{topic}: {payload_str}", flush=True)

def main():
    if len(sys.argv) < 2:
        print("Usage: bin/observe conn_spec")
        sys.exit(1)

    conn_spec_str = sys.argv[1]
    conn_spec = parse_conn_spec(conn_spec_str)
    print(f"Conn spec: scheme={conn_spec.scheme}, host={conn_spec.host}, port={conn_spec.port}, principal={conn_spec.principal}, prefix={conn_spec.prefix}")

    transport = MqttTransport(conn_spec)
    transport.set_on_message(on_message)
    transport.connect()
    transport.subscribe("#")

    try:
        import time
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        transport.disconnect()

if __name__ == '__main__':
    main()
