import os
import subprocess
import time
import sys
import shutil
import argparse
from butler.model_repo import ModelRepository
from butler.blob_repo import BlobRepository

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("conn_spec", nargs="?", help="Connection spec URL")
    args = parser.parse_args()
    
    from butler.conn_spec import get_default_conn_spec
    conn_spec = args.conn_spec or get_default_conn_spec()

    test_dir = "testing"
    if os.path.exists(test_dir):
        shutil.rmtree(test_dir)
    os.makedirs(test_dir)
    
    model_file = os.path.join(test_dir, "test_model.json")
    blobs_dir = os.path.join(test_dir, "blobs")
    
    env = os.environ.copy()
    env["BUTLER_MODEL_FILE"] = model_file
    env["BUTLER_BLOBS_DIR"] = blobs_dir
    env["BUTLER_TIMEOUT"] = "20"
    env["PYTHONPATH"] = os.getcwd()
    
    print(f"Starting Smoke Test with conn_spec: {conn_spec}...")
    
    # Verify argument enforcement
    print("Verifying argument enforcement...")
    for cmd, cmd_args in [
        ("bin/register", []), # missing device_id
        ("bin/trigger", []), # missing device_id, blob_version, blob_path
        ("bin/trigger", ["dev"]), # missing blob_version, blob_path
        ("bin/trigger", ["dev", "1.0"]) # missing blob_path
    ]:
        res = subprocess.run([sys.executable, cmd] + cmd_args, capture_output=True)
        if res.returncode == 0:
            print(f"FAILED: {cmd} {cmd_args} should have failed due to missing arguments.")
            sys.exit(1)
    print("Argument enforcement verified.")

    # Setup
    subprocess.run([sys.executable, "bin/setup", conn_spec], check=True)
    
    # Prepare model and blob
    model_repo = ModelRepository(model_file)
    model_repo.set_device_info("smoke-dev", "main", "vibrant", "butler-v1")
    
    blob_repo = BlobRepository(blobs_dir)
    blob_repo.store_blob("vibrant", "butler-v1", "main", "1.1.0", b"SMOKE_TEST_CONTENT")
    
    # Start components
    butler = subprocess.Popen([sys.executable, "bin/butler", conn_spec], env=env)
    mocket = subprocess.Popen([sys.executable, "bin/mocket", conn_spec, "smoke-dev"], env=env)
    
    try:
        time.sleep(5)
        
        # Trigger update
        print("Triggering update...")
        dummy_blob = os.path.join(test_dir, "dummy.bin")
        with open(dummy_blob, "wb") as f: f.write(b"NEW_VERSION_CONTENT")
        subprocess.run([sys.executable, "bin/trigger", "smoke-dev", "1.1.0", dummy_blob], env=env, check=True)
        
        # Wait and check
        timeout = 60
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
        mocket = subprocess.Popen([sys.executable, "bin/mocket", conn_spec, "smoke-dev", "-f"], env=env)
        
        # Trigger another update
        print("Triggering update (should fail)...")
        with open(dummy_blob, "wb") as f: f.write(b"FAILURE_TEST_CONTENT")
        subprocess.run([sys.executable, "bin/trigger", "smoke-dev", "1.2.0", dummy_blob], env=env, check=True)
        
        # Wait for rollback to 1.1.0
        timeout = 60
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
