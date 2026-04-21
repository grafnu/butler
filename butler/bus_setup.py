import socket
import subprocess
import time

def check_mosquitto():
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex(('localhost', 1883))
        if result == 0:
            print("Mosquitto is running on port 1883.")
            return True
        else:
            print("Mosquitto is NOT running on port 1883.")
            return False
    except Exception as e:
        print(f"Error checking Mosquitto: {e}")
        return False

def try_start_mosquitto():
    print("Attempting to start mosquitto...")
    try:
        # Running mosquitto in background
        subprocess.Popen(["mosquitto"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1)
        return check_mosquitto()
    except Exception as e:
        print(f"Failed to start mosquitto: {e}")
        return False

def main():
    if not check_mosquitto():
        if not try_start_mosquitto():
             print("Please ensure Mosquitto is installed and running.")
             exit(1)
    print("Bus setup complete.")

if __name__ == "__main__":
    main()
