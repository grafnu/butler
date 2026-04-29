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
    
    # 1. Verify argument enforcement
    print("Verifying argument enforcement...")
    cmds = [
        (["bin/register"], "Usage: bin/register device_id"),
        (["bin/mocket"], "the following arguments are required: device_id"),
        (["bin/trigger"], "Usage: bin/trigger device_id blob_version blob_path"),
        (["bin/trigger", "dev1"], "Usage: bin/trigger device_id blob_version blob_path"),
    ]
    for cmd, expected in cmds:
        result = subprocess.run([sys.executable] + cmd, capture_output=True, text=True)
        if expected not in result.stdout and expected not in result.stderr:
            print(f"FAIL: Argument enforcement for {' '.join(cmd)}. Expected '{expected}' in output.")
            sys.exit(1)
    print("Argument enforcement verified.")

    # 2. Setup
    subprocess.run([sys.executable, "bin/setup"], check=True)
    
    # 3. Prepare model and blob
    model_repo = ModelRepository(model_file)
    model_repo.set_device_info("smoke-dev", "main", "vibrant", "butler-v1")
    
    blob_repo = BlobRepository(blobs_dir)
    blob_repo.store_blob("vibrant", "butler-v1", "main", "1.1.0", b"SMOKE_TEST_CONTENT")
    
    # 4. Start components
    butler = subprocess.Popen([sys.executable, "bin/butler"], env=env)
    mocket = subprocess.Popen([sys.executable, "bin/mocket", "smoke-dev"], env=env)
    
    try:
        time.sleep(3)
        
        # 5. Trigger update
        print("Triggering update...")
        subprocess.run([sys.executable, "bin/trigger", "smoke-dev", "1.1.0", "butler/requirements.txt"], env=env, check=True)
        # 6. Wait and check
        timeout = 20
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
