import os
import sys
import subprocess
import time
import json
import paho.mqtt.client as mqtt

def main():
    print("Starting Butler Smoke Test...")
    
    # 1. Setup testing directory
    test_dir = os.path.abspath(os.path.join(os.getcwd(), "testing"))
    os.makedirs(test_dir, exist_ok=True)
    
    model_file = os.path.join(test_dir, "model.json")
    os.environ["BUTLER_MODEL_FILE"] = model_file
    
    # Clean up model file if it exists to start fresh
    if os.path.exists(model_file):
        os.remove(model_file)

    # 2. Verify argument enforcement
    print("Checking argument enforcement...")
    commands_to_check = [
        (["bin/register"], 1),
        (["bin/trigger", "dev-1"], 1),
        (["bin/trigger", "dev-1", "1.1.0"], 1)
    ]
    for cmd, expected_code in commands_to_check:
        # Need to ensure cmd[0] is absolute or relative to root
        full_cmd = [os.path.join(os.getcwd(), cmd[0])] + cmd[1:]
        res = subprocess.run(full_cmd, capture_output=True)
        if res.returncode != expected_code:
            print(f"FAILED: {cmd} returned {res.returncode}, expected {expected_code}")
            sys.exit(1)
    print("Argument enforcement check passed.")

    # 3. Run setup
    print("Running setup...")
    subprocess.run([os.path.join(os.getcwd(), "bin/setup")], check=True)

    # 4. Register device
    device_id = "smoke-dev"
    print(f"Registering device {device_id}...")
    subprocess.run([os.path.join(os.getcwd(), "bin/register"), device_id], check=True)

    # 5. Start background processes
    print("Starting background processes...")
    processes = []
    try:
        # Use -u for unbuffered output to help with logging/debugging if needed
        processes.append(subprocess.Popen([sys.executable, "-u", os.path.join(os.getcwd(), "bin/mocket"), device_id]))
        processes.append(subprocess.Popen([sys.executable, "-u", os.path.join(os.getcwd(), "bin/butler")]))
        processes.append(subprocess.Popen([sys.executable, "-u", os.path.join(os.getcwd(), "bin/verifier")]))

        # Wait for processes to initialize and send first status
        time.sleep(3)

        # 6. Trigger update
        # Use an existing blob as a source
        source_blob = os.path.join(os.getcwd(), "blobs/vibrant/butler-v1/main/1.0.0/firmware.bin")
        if not os.path.exists(source_blob):
            # Create a dummy if it doesn't exist
            os.makedirs(os.path.dirname(source_blob), exist_ok=True)
            with open(source_blob, "wb") as f:
                f.write(b"dummy firmware")

        target_version = "9.9.9-smoke"
        print(f"Triggering update for {device_id} to {target_version}...")
        subprocess.run([os.path.join(os.getcwd(), "bin/trigger"), device_id, target_version, source_blob], check=True)

        # 7. Observe verification
        print("Waiting for verification results on MQTT...")
        verification_results = []
        
        def on_message(client, userdata, msg):
            try:
                data = json.loads(msg.payload.decode())
                res = data.get("payload", {})
                print(f"  [MQTT] Verification: {res.get('result')} - {res.get('message')}")
                verification_results.append(res)
            except Exception as e:
                print(f"Error parsing verification message: {e}")

        client = mqtt.Client()
        client.on_message = on_message
        client.connect("localhost", 1883)
        client.subscribe(f"butler/{device_id}/verify")
        client.loop_start()

        # Wait for a 'pass' that indicates the transition to success
        timeout = 20
        start_time = time.time()
        success = False
        while time.time() - start_time < timeout:
            if any(r.get("result") == "pass" and "PENDING" in r.get("message", "").upper() and "SUCCESS" in r.get("message", "").upper() for r in verification_results):
                success = True
                break
            time.sleep(1)

        client.loop_stop()
        client.disconnect()

        if success:
            print("\n" + "="*20)
            print("SMOKE TEST PASSED")
            print("="*20)
        else:
            print("\n" + "!"*20)
            print("SMOKE TEST FAILED")
            print(f"Captured results: {verification_results}")
            print("!"*20)
            sys.exit(1)

    finally:
        print("Stopping background processes...")
        for p in processes:
            p.terminate()
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()

if __name__ == "__main__":
    main()
