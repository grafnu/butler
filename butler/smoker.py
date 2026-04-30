import subprocess
import time
import os
import sys

def main():
    print("Starting Smoke Test (T01 Refined)...")
    
    # Ensure testing directory exists
    os.makedirs("testing", exist_ok=True)
    
    # Set model file for isolation
    model_file = "testing/model.json"
    os.environ["BUTLER_MODEL_FILE"] = model_file
    if os.path.exists(model_file):
        os.remove(model_file)
    
    # 1. Setup
    print("Running setup...")
    subprocess.run([sys.executable, "-m", "butler.bus_setup"], check=True)
    
    # 2. Start Mocket (System Proxy + Device Conduit)
    print("Starting Mocket...")
    mocket = subprocess.Popen([sys.executable, "-m", "butler.device", "dev1"])
    time.sleep(2)
    
    # 3. Start Verifier
    print("Starting Verifier...")
    verifier = subprocess.Popen([sys.executable, "-m", "butler.verifier"])
    time.sleep(1)
    
    # 4. Start Orchestrator
    print("Starting Orchestrator...")
    orchestrator = subprocess.Popen([sys.executable, "-m", "butler.orchestrator"])
    time.sleep(3)
    
    # 5. Register device
    print("Registering device dev1...")
    from butler.model_repo import ModelRepository
    repo = ModelRepository(model_file=model_file)
    repo.register_device("dev1")
    repo.set_device_info("dev1", "vibrant", "butler-v1", "main")
    
    # 6. Trigger update
    print("Triggering update...")
    from butler.blob_repo import BlobRepository
    # Use testing/blobs as specified
    blob_repo = BlobRepository(base_dir="testing/blobs")
    blob_repo.store_blob("vibrant", "butler-v1", "main", "9.9.9-smoke", b"dummy content")
    repo.set_target_version("dev1", "9.9.9-smoke")
    
    # 7. Wait for update
    print("Waiting for update...")
    start_time = time.time()
    success = False
    while time.time() - start_time < 20:
        device = repo.get_device("dev1")
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
