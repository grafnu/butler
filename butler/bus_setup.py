import socket
import subprocess
import sys
import time

def is_mosquitto_running(host="localhost", port=1883):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex((host, port)) == 0

def main():
    if is_mosquitto_running():
        print("Mosquitto is running on port 1883")
    else:
        print("Mosquitto not detected on port 1883. Attempting to start...")
        try:
            # Start mosquitto in background
            subprocess.Popen(["mosquitto"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(2)
            if is_mosquitto_running():
                print("Mosquitto started successfully.")
            else:
                print("Failed to start Mosquitto. Please start it manually.")
                # We don't exit 1 here if we are in an environment where we can't start it
                # but let's assume we need it.
                sys.exit(1)
        except FileNotFoundError:
            print("Mosquitto binary not found. Please install it.")
            sys.exit(1)
    
    print("Bus setup complete.")

if __name__ == "__main__":
    main()
