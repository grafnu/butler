import sys
import subprocess
import time
import socket
import argparse
from butler.transport import parse_conn_spec

def is_port_open(host, port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex((host, port)) == 0

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

    port = conn_spec.port or 1883

    if conn_spec.scheme == 'mqtt':
        if not is_port_open(conn_spec.host, port):
            if conn_spec.host in ['localhost', '127.0.0.1']:
                print(f"MQTT broker not found on {conn_spec.host}:{port}. Attempting to start mosquitto...")
                try:
                    subprocess.Popen(['mosquitto', '-p', str(port)],
                                   stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL)
                    for _ in range(10):
                        time.sleep(0.5)
                        if is_port_open(conn_spec.host, port):
                            print("Mosquitto started successfully.")
                            break
                    else:
                        print("Failed to start mosquitto.")
                        sys.exit(1)
                except Exception as e:
                    print(f"Error starting mosquitto: {e}")
                    sys.exit(1)
            else:
                print(f"MQTT broker not reachable on {conn_spec.host}:{port}")
                sys.exit(1)
        else:
            print(f"MQTT broker is already running on {conn_spec.host}:{port}")

    print("Bus setup complete.")

if __name__ == '__main__':
    main()
