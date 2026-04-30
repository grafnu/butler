import socket
import subprocess
import sys
import time

def is_mosquitto_running(host="localhost", port=1883):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex((host, port)) == 0

def main():
    if not is_mosquitto_running():
        try:
            subprocess.Popen(["mosquitto"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(2)
        except FileNotFoundError:
            sys.exit(1)
    print("Bus setup complete.")

if __name__ == "__main__":
    main()
