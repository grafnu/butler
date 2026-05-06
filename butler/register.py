import sys
from butler.transport import parse_conn_spec
from butler.model_repo import ModelRepo

def main():
    if len(sys.argv) < 3:
        print("Usage: bin/register conn_spec device_id")
        sys.exit(1)

    conn_spec_str = sys.argv[1]
    device_id = sys.argv[2]

    conn_spec = parse_conn_spec(conn_spec_str)
    print(f"Conn spec: scheme={conn_spec.scheme}, host={conn_spec.host}, port={conn_spec.port}, principal={conn_spec.principal}, prefix={conn_spec.prefix}")

    repo = ModelRepo()
    repo.add_device(device_id)
    print(f"Registered device {device_id} in model.")

if __name__ == '__main__':
    main()
