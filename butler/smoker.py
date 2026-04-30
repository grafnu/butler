import os
import subprocess
import time
import sys
import shutil
from butler.model_repo import ModelRepository
from butler.blob_repo import BlobRepository

def main():
    test_dir = "testing"
    if os.path.exists(test_dir):
        shutil.rmtree(test_dir)
    os.makedirs(test_dir)
    
    model_file = os.path.join(test_dir, "test_model.json")
    blobs_dir = os.path.join(test_dir, "blobs")
    
    env = os.environ.copy()
    env["BUTLER_MODEL_FILE"] = model_file
    env["BUTLER_BLOBS_DIR"] = blobs_dir
    env["PYTHONPATH"] = os.getcwd()
    
    print("Starting Smoke Test...")
    
    # Setup
    subprocess.run([sys.executable, "bin/setup"], check=True)
    
    # Prepare model and blob
    model_repo = ModelRepository(model_file)
    model_repo.set_device_info("smoke-dev", "main", "vibrant", "butler-v1")
    
    blob_repo = BlobRepository(blobs_dir)
    blob_repo.store_blob("vibrant", "butler-v1", "main", "1.1.0", b"SMOKE_TEST_CONTENT")
    
    # Start components
    butler = subprocess.Popen([sys.executable, "bin/butler"], env=env)
    mocket = subprocess.Popen([sys.executable, "bin/mocket", "smoke-dev"], env=env)
    
    try:
        time.sleep(5)
        
        # Trigger update
        print("Triggering update...")
        subprocess.run([sys.executable, "bin/trigger", "smoke-dev", "1.1.0", "butler/requirements.txt"], env=env, check=True)
        
        # Wait and check
        timeout = 30
        start_time = time.time()
        passed = False
        while time.time() - start_time < timeout:
            model_repo.reload()
            state = model_repo.get_device_state("smoke-dev", "main")
            if state and state.get("current_version") == "1.1.0":
                passed = True
                break
            time.sleep(1)
        
        if passed:
            print("Smoke test passed!")
        else:
            print("Smoke test FAILED: Timeout waiting for update.")
            sys.exit(1)
            
    finally:
        butler.terminate()
        mocket.terminate()
        butler.wait()
        mocket.wait()

if __name__ == "__main__":
    main()
