import sys
import subprocess
import time
import socket
from butler.transport import parse_conn_spec, MqttTransport

def is_port_open(host, port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex((host, port)) == 0

def main():
    if len(sys.argv) < 2:
        print("Usage: bin/setup conn_spec")
        sys.exit(1)

    conn_spec_str = sys.argv[1]
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
                    # Wait for it to start
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
