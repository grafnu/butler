import os
import time
import subprocess
import signal
import sys
from butler.blob_repo import BlobRepository
from butler.model_repo import ModelRepository

def setup_data(model_file):
    print(f"Setting up test data in {model_file}...")
    if not os.path.exists("blobs"):
         os.makedirs("blobs")
    
    # Create dummy firmware files to trigger with
    with open("fw_v1.0.0.bin", "wb") as f: f.write(b"FIRMWARE_V1.0.0_CONTENT")
    with open("fw_v1.1.0.bin", "wb") as f: f.write(b"FIRMWARE_V1.1.0_CONTENT")
    with open("fw_v9.9.9.bin", "wb") as f: f.write(b"FIRMWARE_V9.9.9_CONTENT_FAIL")

    model_repo = ModelRepository(model_file)
    # Reset model
    model_repo.data = {"devices": {}}
    model_repo.set_device_info("dev-001", "main", "vibrant", "butler-v1")
    model_repo.set_target_version("dev-001", "main", "1.0.0")
    
    # Store the 1.0.0 blob so it's ready
    blob_repo = BlobRepository()
    blob_repo.store_blob("vibrant", "butler-v1", "main", "1.0.0", b"FIRMWARE_V1.0.0_CONTENT")
    
    # Mark as current 1.0.0
    model_repo.update_current_version("dev-001", "main", "1.0.0")

def main():
    demo_model = "demo_model.json"
    if os.path.exists(demo_model):
        os.remove(demo_model)
    
    setup_data(demo_model)
    
    # Ensure bus is ready
    subprocess.run([sys.executable, "bin/setup"])
    
    processes = []
    
    env = os.environ.copy()
    env["PYTHONPATH"] = os.getcwd()
    env["BUTLER_MODEL_FILE"] = demo_model
    
    print("Launching Orchestrator...")
    orchestrator = subprocess.Popen([sys.executable, "-u", "bin/butler"], env=env, stdout=sys.stdout, stderr=sys.stderr)
    processes.append(orchestrator)
    
    print("Launching Verifier...")
    verifier = subprocess.Popen([sys.executable, "-u", "bin/verifier"], env=env, stdout=sys.stdout, stderr=sys.stderr)
    processes.append(verifier)
    
    time.sleep(1)
    
    print("Launching Device...")
    device = subprocess.Popen([sys.executable, "-u", "bin/mocket", "dev-001"], env=env, stdout=sys.stdout, stderr=sys.stderr)
    processes.append(device)
    
    time.sleep(2)
    
    # 1. Successful Update to 1.1.0
    print("\n>>> Triggering update to 1.1.0...")
    subprocess.run([sys.executable, "bin/trigger", "dev-001", "1.1.0", "fw_v1.1.0.bin"], env=env)
    
    # Wait for update to complete
    time.sleep(10)
    
    # 2. Failed Update to 9.9.9 (triggers rollback to 1.1.0)
    print("\n>>> Triggering FAILED update to 9.9.9...")
    subprocess.run([sys.executable, "bin/trigger", "dev-001", "9.9.9", "fw_v9.9.9.bin"], env=env)
    
    # Wait for failure and rollback
    time.sleep(15)
    
    print("\nDemo complete. Checking final state...")
    model_repo = ModelRepository(demo_model)
    state = model_repo.get_device_state("dev-001", "main")
    print(f"Final State: Current={state['current_version']}, Target={state['target_version']}")
    
    # Cleanup
    for p in processes:
        p.terminate()
        p.wait()
    
    if os.path.exists(demo_model):
        os.remove(demo_model)
    
    for f in ["fw_v1.0.0.bin", "fw_v1.1.0.bin", "fw_v9.9.9.bin"]:
         if os.path.exists(f): os.remove(f)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
