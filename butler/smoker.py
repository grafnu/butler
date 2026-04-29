import subprocess
import time
import os
import sys

def main():
    print("Starting Smoke Test (T03 Final)...")
    
    # 1. Setup
    print("Running setup...")
    subprocess.run([sys.executable, "-m", "butler.bus_setup"], check=True)
    
    # 2. Start Verifier (System Proxy)
    print("Starting Verifier...")
    verifier = subprocess.Popen([sys.executable, "-m", "butler.verifier"])
    time.sleep(2)
    
    # 3. Start Orchestrator
    print("Starting Orchestrator...")
    orchestrator = subprocess.Popen([sys.executable, "-m", "butler.orchestrator"])
    time.sleep(2)
    
    # 4. Start Mocket
    print("Starting Mocket...")
    mocket = subprocess.Popen([sys.executable, "-m", "butler.device", "dev1"])
    time.sleep(2)
    
    # 5. Register device in model directly (CLI tool behavior)
    print("Registering device dev1...")
    from butler.model_repo import ModelRepository
    ModelRepository().register_device("dev1")
    
    # 6. Trigger update directly (CLI tool behavior)
    print("Triggering update...")
    from butler.blob_repo import BlobRepository
    BlobRepository().store_blob("default", "default", "default", "1.1", b"dummy content")
    ModelRepository().update_device("dev1", target_version="1.1")
    
    # 7. Wait for update
    print("Waiting for update...")
    start_time = time.time()
    success = False
    while time.time() - start_time < 20:
        device = ModelRepository().get_device("dev1")
        if device.get("current_version") == "1.1":
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
