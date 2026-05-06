import subprocess
import time
import os
import sys
import json

from butler.common import get_default_conn_spec

def run_command(args, env=None):
    return subprocess.run(args, capture_output=True, text=True, env=env)

def main():
    if len(sys.argv) < 2:
        print("Usage: bin/smokeit conn_spec")
        sys.exit(1)
    conn_spec = sys.argv[1]
    print(f"Starting Smoke Test with conn_spec: {conn_spec}")
    
    # Ensure testing directory exists
    os.makedirs("testing", exist_ok=True)
    
    # Set model file for isolation
    model_file = "testing/model.json"
    os.environ["BUTLER_MODEL_FILE"] = model_file
    if os.path.exists(model_file):
        os.remove(model_file)
    
    # 1. Verify Argument Enforcement
    print("Verifying argument enforcement...")
    
    # register requires registry_id and device_id
    res = run_command(["bin/register"])
    if res.returncode == 0:
        print("FAIL: bin/register should require arguments")
        sys.exit(1)
        
    # trigger requires registry_id, device_id, blob_version, blob_path
    res = run_command(["bin/trigger", "reg1", "dev1", "1.1"])
    if res.returncode == 0:
        print("FAIL: bin/trigger should require 4 arguments")
        sys.exit(1)

    # mocket requires conn_spec, registry_id, and device_id
    res = run_command(["bin/mocket"])
    if res.returncode == 0:
        print("FAIL: bin/mocket should require arguments")
        sys.exit(1)

    print("Argument enforcement verified.")

    # 2. Setup
    print("Running setup...")
    subprocess.run(["bin/setup", conn_spec], check=True)
    
    # 3. Start Mocket (Client/UDMIS) first so it can handle model requests
    print("Starting Mocket...")
    mocket = subprocess.Popen(["bin/mocket", conn_spec, "reg1", "dev1"])
    time.sleep(5)

    # 4. Start Orchestrator (System)
    print("Starting Orchestrator...")
    orchestrator = subprocess.Popen(["bin/butler", conn_spec])
    time.sleep(5)

    # 5. Start Verifier
    print("Starting Verifier...")
    verifier = subprocess.Popen(["bin/verifier", conn_spec])
    time.sleep(2)

    # 6. Register device dev1...

    print("Registering device dev1...")
    subprocess.run(["bin/register", "reg1", "dev1"], check=True)
    
    # 7. Trigger update
    print("Triggering update...")
    blob_path = "testing/blobs/dummy.bin"
    os.makedirs(os.path.dirname(blob_path), exist_ok=True)
    with open(blob_path, "wb") as f:
        f.write(b"smoke test blob")
        
    subprocess.run(["bin/trigger", "reg1", "dev1", "9.9.9-smoke", blob_path], check=True)
    
    # 8. Wait for update
    print("Waiting for update...")
    start_time = time.time()
    success = False
    
    from butler.model_repo import ModelRepository
    repo = ModelRepository(model_file=model_file)
    
    while time.time() - start_time < 30:
        device = repo.get_subsystem("dev1", "main")
        if device.get("current_version") == "9.9.9-smoke":
            print("Update success detected in model!")
            success = True
            break
        time.sleep(1)
    
    # Cleanup
    print("Cleaning up...")
    orchestrator.terminate()
    mocket.terminate()
    verifier.terminate()
    
    if success:
        print("Smoke test PASSED")
    else:
        print("Smoke test FAILED")
        sys.exit(1)

if __name__ == "__main__":
    main()
