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
    env["BUTLER_TIMEOUT"] = "5"
    env["PYTHONPATH"] = os.getcwd()
    
    print("Starting Smoke Test...")
    
    # Verify argument enforcement
    print("Verifying argument enforcement...")
    for cmd, args in [
        ("bin/register", []),
        ("bin/mocket", []),
        ("bin/trigger", ["dev"]),
        ("bin/trigger", ["dev", "1.0"])
    ]:
        res = subprocess.run([sys.executable, cmd] + args, capture_output=True)
        if res.returncode == 0:
            print(f"FAILED: {cmd} {args} should have failed due to missing arguments.")
            sys.exit(1)
    print("Argument enforcement verified.")

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
            print("Basic update smoke test passed!")
        else:
            print("Basic update smoke test FAILED: Timeout waiting for update.")
            sys.exit(1)

        # Failure mode test
        print("Starting Failure Mode Test...")
        mocket.terminate()
        mocket.wait()
        
        # Start mocket in failure mode
        mocket = subprocess.Popen([sys.executable, "bin/mocket", "smoke-dev", "-f"], env=env)
        
        # Trigger another update
        print("Triggering update (should fail)...")
        blob_repo.store_blob("vibrant", "butler-v1", "main", "1.2.0", b"FAILURE_TEST_CONTENT")
        subprocess.run([sys.executable, "bin/trigger", "smoke-dev", "1.2.0", "butler/requirements.txt"], env=env, check=True)
        
        # Wait for rollback to 1.1.0
        timeout = 30
        start_time = time.time()
        rolled_back = False
        while time.time() - start_time < timeout:
            model_repo.reload()
            state = model_repo.get_device_state("smoke-dev", "main")
            if state and state.get("target_version") == "1.1.0":
                rolled_back = True
                break
            time.sleep(1)
        
        if rolled_back:
            print("Failure mode rollback passed!")
        else:
            print("Failure mode rollback FAILED: Timeout waiting for rollback.")
            sys.exit(1)
            
    finally:
        butler.terminate()
        mocket.terminate()
        butler.wait()
        mocket.wait()

if __name__ == "__main__":
    main()
