import sys
from butler.transport import parse_conn_spec, MqttTransport

def main():
    if len(sys.argv) < 2:
        print("Usage: bin/setup conn_spec")
        sys.exit(1)

    conn_spec_str = sys.argv[1]
    conn_spec = parse_conn_spec(conn_spec_str)
    print(f"Conn spec: scheme={conn_spec.scheme}, host={conn_spec.host}, port={conn_spec.port}, principal={conn_spec.principal}, prefix={conn_spec.prefix}")

    if conn_spec.scheme == 'mqtt':
        try:
            transport = MqttTransport(conn_spec)
            transport.connect()
            transport.disconnect()
            print(f"Mosquitto is running on port {conn_spec.port or 1883}")
        except Exception as e:
            print(f"Failed to connect to MQTT broker: {e}")
            sys.exit(1)

    print("Bus setup complete.")

if __name__ == '__main__':
    main()
