import subprocess
import time
import socket
import os

def is_broker_running(host='localhost', port=1883):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex((host, port)) == 0

def main():
    port = 1883
    if is_broker_running(port=port):
        print("Bus setup complete.")
        return

    print(f"MQTT broker not found on port {port}. Attempting to start mosquitto...")
    try:
        # Start mosquitto in the background
        # We use -d to daemonize, but some environments might not support it well, 
        # so we'll just use Popen and let it run.
        subprocess.Popen(['mosquitto', '-p', str(port)], 
                         stdout=subprocess.DEVNULL, 
                         stderr=subprocess.DEVNULL)
        
        # Wait a bit for it to start
        for _ in range(5):
            time.sleep(1)
            if is_broker_running(port=port):
                print("Bus setup complete.")
                return
    except FileNotFoundError:
        print("Error: 'mosquitto' command not found. Please install it.")
        return
    except Exception as e:
        print(f"Error starting mosquitto: {e}")
        return

    if not is_broker_running(port=port):
        print(f"Error: Could not verify MQTT broker is running on port {port}.")

if __name__ == "__main__":
    main()
