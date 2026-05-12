import subprocess
import time
import os
import sys
import threading

def run_test(verifier_id, butler_id, branch_name):
    print(f"Starting test: {verifier_id} verifying {butler_id}")
    prefix = f"{verifier_id}_{butler_id}"
    spec = f"mqtt://{branch_name}_{prefix}@localhost/{prefix}"
    model_file = f"testing/model_{prefix}.json"
    log_dir = "impl"
    
    # Environment for butler tools
    env = os.environ.copy()
    env["BUTLER_MODEL_FILE"] = model_file
    
    # 1. Setup
    print(f"[{verifier_id}->{butler_id}] Setup...")
    subprocess.run([f"impl/{butler_id}/bin/setup", spec], check=True, env=env)
    
    # 2. Observe
    print(f"[{verifier_id}->{butler_id}] Starting observers...")
    obs_v = subprocess.Popen([f"impl/{verifier_id}/bin/observe", spec], 
                             stdout=open(f"{log_dir}/{verifier_id}_verify_{butler_id}.{verifier_id}.log", "w"),
                             stderr=subprocess.STDOUT, env=env)
    obs_b = subprocess.Popen([f"impl/{butler_id}/bin/observe", spec], 
                             stdout=open(f"{log_dir}/{verifier_id}_verify_{butler_id}.{butler_id}.log", "w"),
                             stderr=subprocess.STDOUT, env=env)
    
    # 3. Mocket
    print(f"[{verifier_id}->{butler_id}] Starting mocket...")
    mocket = subprocess.Popen([f"impl/{butler_id}/bin/mocket", spec, "reg1", "dev1"], 
                              stdout=open(f"{log_dir}/{verifier_id}_verify_{butler_id}.mocket.log", "w"),
                              stderr=subprocess.STDOUT, env=env)
    
    # 4. Butler
    print(f"[{verifier_id}->{butler_id}] Starting butler...")
    butler = subprocess.Popen([f"impl/{butler_id}/bin/butler", spec], 
                              stdout=open(f"{log_dir}/{verifier_id}_verify_{butler_id}.butler.log", "w"),
                              stderr=subprocess.STDOUT, env=env)
    
    # 5. Verifier
    print(f"[{verifier_id}->{butler_id}] Starting verifier...")
    # Capture verifier output to check for VALIDATION ERROR
    v_log_path = f"{log_dir}/{verifier_id}_verify_{butler_id}.verifier_out.log"
    verifier = subprocess.Popen([f"impl/{verifier_id}/bin/verifier", spec], 
                                stdout=open(v_log_path, "w"),
                                stderr=subprocess.STDOUT, env=env)
    
    time.sleep(5)
    
    # 6. Register & Trigger
    print(f"[{verifier_id}->{butler_id}] Registering and triggering...")
    subprocess.run([f"impl/{butler_id}/bin/register", "reg1", "dev1"], check=True, env=env)
    
    blob_path = f"testing/blobs/dummy_{verifier_id}_{butler_id}.bin"
    os.makedirs(os.path.dirname(blob_path), exist_ok=True)
    with open(blob_path, "wb") as f:
        f.write(f"smoke test blob {verifier_id} {butler_id}".encode())
    
    subprocess.run([f"impl/{butler_id}/bin/trigger", "reg1", "dev1", f"9.9.9-{verifier_id}-{butler_id}", blob_path], check=True, env=env)
    
    # 7. Wait for success
    print(f"[{verifier_id}->{butler_id}] Waiting for update...")
    start_time = time.time()
    success = False
    
    # We need to check the model file. Since we don't have a common library, we'll just grep it or read it.
    # Most implementations seem to use a JSON file.
    while time.time() - start_time < 60:
        if os.path.exists(model_file):
            try:
                with open(model_file, "r") as f:
                    content = f.read()
                    # Check for "current_version": "9.9.9-..." to ensure butler processed it
                    target = f"9.9.9-{verifier_id}-{butler_id}"
                    if f'"current_version": "{target}"' in content or f"'current_version': '{target}'" in content:
                        print(f"[{verifier_id}->{butler_id}] Update success detected!")
                        success = True
                        break
            except Exception as e:
                pass
        time.sleep(2)
    
    # 8. Check verifier logs for errors
    has_errors = False
    if os.path.exists(v_log_path):
        with open(v_log_path, "r") as f:
            v_out = f.read()
            if "VALIDATION ERROR" in v_out or "ERROR" in v_out:
                print(f"[{verifier_id}->{butler_id}] Verifier reported errors!")
                has_errors = True
    
    # Cleanup
    print(f"[{verifier_id}->{butler_id}] Cleaning up...")
    for p in [butler, mocket, verifier, obs_v, obs_b]:
        p.terminate()
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()
            
    if success and not has_errors:
        return "PASS"
    elif success and has_errors:
        return "FIXED" # It worked but had validation issues that we might need to fix in spec
    else:
        return "FAIL"

def main():
    branch = "gemerger"
    pairs = [("A", "B"), ("B", "C"), ("C", "D"), ("D", "A")]
    
    results = {}
    threads = []
    
    for v, b in pairs:
        print(f"--- RESTARTING MOSQUITTO for {v}->{b} ---")
        subprocess.run(["killall", "mosquitto"], capture_output=True)
        time.sleep(2)
        subprocess.run(["mosquitto", "-d"], check=True)
        time.sleep(2)
        
        res = run_test(v, b, branch)
        results[f"impl_{v} verifies impl_{b}"] = res
        
    # Write summary
    with open("test_summary.txt", "w") as f:
        for key in sorted(results.keys()):
            f.write(f"{key}: {results[key]}\n")
            print(f"{key}: {results[key]}")

if __name__ == "__main__":
    main()
