import subprocess
import time
import os
import sys
import json
import paho.mqtt.client as mqtt
from butler.common import ButlerMessage

class SmokeTester:
    def __init__(self, device_id="smoke-dev-001"):
        self.device_id = device_id
        self.processes = []
        self.messages_received = {
            "status": False,
            "update_payload": False,
            "verify_pass": False
        }
        self.success = False

    def on_message(self, client, userdata, msg):
        topic = msg.topic
        try:
            data = json.loads(msg.payload.decode())
            print(f"[SMOKER] Received message on {topic}")
            if "status" in topic:
                self.messages_received["status"] = True
            elif "update_payload" in topic:
                self.messages_received["update_payload"] = True
            elif "verify" in topic:
                if data["payload"].get("result") == "PASS":
                    self.messages_received["verify_pass"] = True
                else:
                    print(f"[SMOKER] Received FAIL verification: {data['payload'].get('message')}")
        except Exception as e:
            print(f"[SMOKER] Error parsing message: {e}")

    def verify_usage(self):
        print("[SMOKER] Verifying argument enforcement...")
        # Check mocket
        res = subprocess.run(["bin/mocket"], capture_output=True, text=True)
        if res.returncode == 0:
            print("[SMOKER] FAIL: mocket should have failed without arguments")
            return False
        
        # Check trigger
        res = subprocess.run(["bin/trigger"], capture_output=True, text=True)
        if res.returncode == 0:
            print("[SMOKER] FAIL: trigger should have failed without arguments")
            return False
        
        # Check register
        res = subprocess.run(["bin/register"], capture_output=True, text=True)
        if res.returncode == 0:
            print("[SMOKER] FAIL: register should have failed without arguments")
            return False
        
        print("[SMOKER] Argument enforcement verified.")
        return True

    def run(self):
        print("[SMOKER] Starting Smoke Test...")
        
        if not self.verify_usage():
            print("[SMOKER] SMOKE TEST FAILED: Argument enforcement check failed")
            sys.exit(1)

        # Create testing directory if it doesn't exist
        test_dir = "testing"
        if not os.path.exists(test_dir):
            os.makedirs(test_dir)

        # Use a temporary model file for the smoke test
        smoke_model = os.path.join(test_dir, "smoke_model.json")
        if os.path.exists(smoke_model):
            os.remove(smoke_model)
        
        # 1. Setup
        print("[SMOKER] Running bin/setup...")
        subprocess.run(["bin/setup"], check=True)

        # 2. Start MQTT Listener
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, "smoke-tester")
        client.on_message = self.on_message
        client.connect("localhost", 1883, 60)
        client.subscribe("butler/#")
        client.loop_start()

        env = os.environ.copy()
        env["PYTHONPATH"] = os.getcwd()
        env["BUTLER_MODEL_FILE"] = smoke_model

        # Initialize device in model BEFORE starting butler
        from butler.model_repo import ModelRepository
        model = ModelRepository(smoke_model)
        model.set_device_info(self.device_id, "main", "vibrant", "butler-v1")
        model.update_current_version(self.device_id, "main", "1.0.0")
        model.set_target_version(self.device_id, "main", "1.0.0")
        
        try:
            # 3. Start Butler (Orchestrator)
            print("[SMOKER] Starting bin/butler...")
            p_butler = subprocess.Popen(["bin/butler"], env=env, stdout=sys.stdout, stderr=sys.stderr)
            self.processes.append(p_butler)

            # 4. Start Verifier
            print("[SMOKER] Starting bin/verifier...")
            p_verifier = subprocess.Popen(["bin/verifier"], env=env, stdout=sys.stdout, stderr=sys.stderr)
            self.processes.append(p_verifier)

            # 5. Start Mocket
            print(f"[SMOKER] Starting bin/mocket {self.device_id}...")
            p_mocket = subprocess.Popen(["bin/mocket", self.device_id], env=env, stdout=sys.stdout, stderr=sys.stderr)
            self.processes.append(p_mocket)

            time.sleep(2) # Wait for startup

            # 6. Trigger update
            # Create a temporary blob file for the test
            blob_path = os.path.join(test_dir, "smoke_test_blob.bin")
            with open(blob_path, "wb") as f:
                f.write(b"SMOKE_TEST_CONTENT_V1.1.0")

            print(f"[SMOKER] Triggering update for {self.device_id} to 1.1.0...")
            subprocess.run(["bin/trigger", self.device_id, "1.1.0", blob_path], env=env, check=True)

            # Cleanup temp blob
            if os.path.exists(blob_path):
                os.remove(blob_path)

            # 7. Wait and check
            print("[SMOKER] Waiting for message exchange...")
            timeout = 20
            start_time = time.time()
            while time.time() - start_time < timeout:
                if all(self.messages_received.values()):
                    print("[SMOKER] All expected message types received!")
                    self.success = True
                    break
                time.sleep(0.5)

            if not self.success:
                print("[SMOKER] Smoke test TIMEOUT. Missing messages:")
                for k, v in self.messages_received.items():
                    if not v:
                        print(f"  - {k}")

        finally:
            print("[SMOKER] Cleaning up...")
            client.loop_stop()
            for p in self.processes:
                p.terminate()
                p.wait()
            if os.path.exists(smoke_model):
                os.remove(smoke_model)

        if self.success:
            print("[SMOKER] SMOKE TEST PASSED")
            sys.exit(0)
        else:
            print("[SMOKER] SMOKE TEST FAILED")
            sys.exit(1)

def main():
    tester = SmokeTester()
    tester.run()

if __name__ == "__main__":
    main()
