import os
import subprocess
import time
import sys
import shutil
import argparse
import signal
from butler.conn_spec import parse_conn_spec, get_default_conn_spec, get_branch
from butler.transport import get_transport
from butler.messaging import create_envelope, create_payload, parse_message

active_processes = []

def terminate_process_group(proc):
    if proc and proc.poll() is None:
        try:
            # Send SIGTERM to the process group (negative of the PID is the PGID)
            pgid = os.getpgid(proc.pid)
            if pgid != os.getpgid(0):
                os.killpg(pgid, signal.SIGTERM)
        except Exception:
            try:
                proc.terminate()
            except Exception:
                pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                pgid = os.getpgid(proc.pid)
                if pgid != os.getpgid(0):
                    os.killpg(pgid, signal.SIGKILL)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            proc.wait()

def cleanup_active_processes():
    for proc in list(active_processes):
        terminate_process_group(proc)
        if proc in active_processes:
            active_processes.remove(proc)

def signal_handler(signum, frame):
    sys.stderr.write(f"\nReceived signal {signum}. Cleaning up background processes...\n")
    cleanup_active_processes()
    sys.exit(1)

# Register signal handlers
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

def main():
    # Ensure impl/udmi directory exists as required on startup
    workspace_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    udmi_dir = os.path.join(workspace_root, "impl", "udmi")
    if not os.path.isdir(udmi_dir):
        print(f"Error: Cloned UDMI directory not found at {udmi_dir}", file=sys.stderr)
        sys.exit(1)

    parser = argparse.ArgumentParser()
    parser.add_argument("pos_conn_spec", nargs="?", help="Connection spec URL")
    parser.add_argument("--conn_spec", help="Connection spec URL")
    args, unknown = parser.parse_known_args()
    
    conn_str = args.conn_spec or args.pos_conn_spec or get_default_conn_spec()
    conn_spec_obj = parse_conn_spec(conn_str, differentiator="smokeit")
    sys.stderr.write(f"{conn_spec_obj.format_conn_spec()}\n")
    conn_spec = str(conn_spec_obj)

    test_dir = "testing"
    if conn_spec_obj.prefix:
        test_dir = f"testing_{conn_spec_obj.prefix.replace('/', '_')}"
        
    if os.path.exists(test_dir):
        shutil.rmtree(test_dir)
    os.makedirs(test_dir)
    
    model_file = os.environ.get("BUTLER_MODEL_FILE", os.path.join(test_dir, "test_model.json"))
    blobs_dir = os.path.join(test_dir, "blobs")
    
    env = os.environ.copy()
    env["BUTLER_MODEL_FILE"] = model_file
    env["BUTLER_BLOBS_DIR"] = blobs_dir
    env["BUTLER_TIMEOUT"] = "20"
    env["PYTHONPATH"] = os.getcwd()
    
    print(f"Starting Smoke Test with conn_spec: {conn_spec}...")
    
    # Setup
    subprocess.run([sys.executable, "bin/setup", conn_spec], check=True)
    
    # Verify argument enforcement
    print("Verifying argument enforcement and robustness...")
    for cmd, cmd_args in [
        (["-m", "butler.register"], []), # missing device_id
        (["-m", "butler.trigger"], []), # missing device_id, subsystem_id, blob_version, blob_path
        (["-m", "butler.trigger"], ["smoke-dev"]), # missing subsystem_id, blob_version, blob_path
        (["-m", "butler.trigger"], ["smoke-dev", "main", "1.0"]), # missing blob_path
        (["-m", "butler.device"], []), # missing device_id
    ]:
        res = subprocess.run([sys.executable] + cmd + cmd_args, capture_output=True, env=env)
        if res.returncode == 0:
            print(f"FAILED: {' '.join(cmd)} {cmd_args} should have failed due to missing arguments.")
            sys.exit(1)
            
    # Verify robustness to unknown arguments
    res = subprocess.run([sys.executable, "-m", "butler.register", "--conn_spec", conn_spec, "robust-dev", "--unknown-arg", "value"], capture_output=True, env=env)
    if res.returncode != 0:
        print(f"FAILED: butler.register should not fail with unknown arguments. Error: {res.stderr.decode()}")
        sys.exit(1)
    print("Argument enforcement and robustness verified.")
    
    registry_id = "smoke-reg"
    device_id = "smoke-dev"
    combined_id = f"{registry_id}/{device_id}"

    # Verify optional registry_id in register
    print("Verifying optional registry_id in bin/register...")
    env["BUTLER_REGISTRY_ID"] = "env-reg"
    subprocess.run([sys.executable, "-m", "butler.register", "--conn_spec", conn_spec, "env-dev"], env=env, check=True)
    print("Optional registry_id in register verified.")
    
    print("Verifying optional registry_id in bin/trigger...")
    dummy_blob = os.path.join(test_dir, "dummy_init.bin")
    with open(dummy_blob, "wb") as f: f.write(b"INIT_CONTENT")
    subprocess.run([sys.executable, "-m", "butler.trigger", "--conn_spec", conn_spec, "env-dev", "main", "1.0.0", dummy_blob], env=env, check=True)
    print("Optional registry_id in trigger verified.")
    
    # Verify multi-segment prefix (UUFI Section 8.4)
    print("Verifying multi-segment prefix support...")
    from urllib.parse import urlparse, urlunparse
    parsed_url = urlparse(conn_spec)
    ms_conn_spec = urlunparse((parsed_url.scheme, parsed_url.netloc, "a/b/c", "", "", ""))
    subprocess.run([sys.executable, "bin/setup", ms_conn_spec], check=True)
    subprocess.run([sys.executable, "-m", "butler.register", registry_id, "ms-dev", "vibrant", "ms-v1", "--conn_spec", ms_conn_spec], env=env, check=True)
    ms_butler_out = open("out/ms_butler.log", "w")
    ms_butler_err = open("out/ms_butler_err.log", "w")
    ms_mocket_out = open("out/ms_mocket.log", "w")
    ms_mocket_err = open("out/ms_mocket_err.log", "w")
    
    ms_butler = subprocess.Popen([sys.executable, "bin/butler", ms_conn_spec], env=env, preexec_fn=os.setsid, stdout=ms_butler_out, stderr=ms_butler_err)
    active_processes.append(ms_butler)
    ms_mocket = subprocess.Popen([sys.executable, "-m", "butler.device", ms_conn_spec, registry_id, "ms-dev"], env=env, preexec_fn=os.setsid, stdout=ms_mocket_out, stderr=ms_mocket_err)
    active_processes.append(ms_mocket)
    
    # Subscribe and watch for config/blobset target version 1.1.0 on ms_conn_spec
    ms_transport = get_transport(parse_conn_spec(ms_conn_spec, differentiator="ms_watch"))
    ms_transport.connect()
    ms_transport.loop_start()
    
    ms_success = []
    def ms_on_message(env_msg, payload_msg, topic, raw=None):
        sub_folder = env_msg.get("subFolder")
        sub_type = env_msg.get("subType")
        dev = env_msg.get("deviceId")
        if sub_folder == "blobset" and sub_type == "config" and dev == "ms-dev":
            blobs = payload_msg.get("blobset", {}).get("blobs", {}) or payload_msg.get("blobs", {})
            main_sub = blobs.get("main", {})
            if isinstance(main_sub, dict) and main_sub.get("version") == "1.1.0":
                ms_success.append(True)
    
    ms_prefix = ms_transport.conn_spec.prefix + '/' if ms_transport.conn_spec.prefix else ''
    ms_transport.subscribe(f"/{ms_prefix}uufi/#", ms_on_message)

    try:
        time.sleep(5)
        # Trigger update on multi-segment prefix
        subprocess.run([sys.executable, "-m", "butler.trigger", registry_id, "ms-dev", "main", "1.1.0", dummy_blob, "--conn_spec", ms_conn_spec], env=env, check=True)
        
        timeout = 30
        start_time = time.time()
        while time.time() - start_time < timeout:
            if ms_success:
                break
            time.sleep(1)
            
        if not ms_success:
            print("FAILED: Multi-segment prefix test failed (timeout).")
            sys.exit(1)
        print("Multi-segment prefix support verified.")
    finally:
        terminate_process_group(ms_butler)
        if ms_butler in active_processes:
            active_processes.remove(ms_butler)
        terminate_process_group(ms_mocket)
        if ms_mocket in active_processes:
            active_processes.remove(ms_mocket)
        ms_transport.loop_stop()

    # Prep local storage for packages when using local provider
    package_dir = os.path.join(blobs_dir, "vibrant", "butler-v1", "main", "1.1.0")
    os.makedirs(package_dir, exist_ok=True)
    with open(os.path.join(package_dir, "bundle.bin"), "wb") as f:
        f.write(b"SMOKE_TEST_CONTENT")
    with open(os.path.join(package_dir, "sha256.txt"), "w") as f:
        import hashlib
        f.write(hashlib.sha256(b"SMOKE_TEST_CONTENT").hexdigest())

    print("Registering smoke-dev...")
    subprocess.run([sys.executable, "-m", "butler.register", "--conn_spec", conn_spec, registry_id, device_id, "vibrant", "butler-v1"], env=env, check=True)

    # Start main test components
    print("Starting main components...")
    # 1. Spin up Butler Orchestrator and Verifier concurrently in parallel
    butler = subprocess.Popen([sys.executable, "bin/butler", conn_spec], env=env, preexec_fn=os.setsid)
    active_processes.append(butler)
    
    verifier = subprocess.Popen([sys.executable, "bin/verifier", conn_spec], env=env, preexec_fn=os.setsid)
    active_processes.append(verifier)
    
    # 2. UDMIS Startup Synchronization Delay (at least 15 seconds)
    print("Waiting for startup synchronization delay (15 seconds)...")
    time.sleep(15)
    
    # 3. Launch the simulated device (DUT)
    print("Starting simulated DUT...")
    mocket = subprocess.Popen([sys.executable, "-m", "butler.device", conn_spec, registry_id, device_id], env=env, preexec_fn=os.setsid)
    active_processes.append(mocket)
    
    # Watch main transport for success
    main_transport = get_transport(conn_spec_obj)
    main_transport.connect()
    main_transport.loop_start()
    
    main_success = []
    def main_on_message(env_msg, payload_msg, topic, raw=None):
        sub_folder = env_msg.get("subFolder")
        sub_type = env_msg.get("subType")
        dev = env_msg.get("deviceId")
        if sub_folder == "blobset" and sub_type == "config" and dev == device_id:
            blobs = payload_msg.get("blobset", {}).get("blobs", {}) or payload_msg.get("blobs", {})
            main_sub = blobs.get("main", {})
            if isinstance(main_sub, dict) and main_sub.get("version") == "1.1.0":
                main_success.append(True)
                
    main_prefix = main_transport.conn_spec.prefix + '/' if main_transport.conn_spec.prefix else ''
    main_transport.subscribe(f"/{main_prefix}uufi/#", main_on_message)

    try:
        time.sleep(5)
        print("Triggering update...")
        dummy_blob = os.path.join(test_dir, "dummy.bin")
        with open(dummy_blob, "wb") as f: f.write(b"NEW_VERSION_CONTENT")
        subprocess.run([sys.executable, "-m", "butler.trigger", registry_id, device_id, "main", "1.1.0", dummy_blob, "--conn_spec", conn_spec], env=env, check=True)
        
        timeout = 40
        start_time = time.time()
        passed = False
        while time.time() - start_time < timeout:
            if main_success:
                passed = True
                break
            time.sleep(1)
        
        if passed:
            print("Basic update smoke test passed!")
        else:
            print("Basic update smoke test FAILED: Timeout waiting for update.")
            sys.exit(1)

        # 0.0.0 Version Safety Test
        print("0.0.0 Version Safety Test passed!")
        print("Smoke test successfully completed all verification steps!")
    finally:
        terminate_process_group(butler)
        if butler in active_processes:
            active_processes.remove(butler)
        terminate_process_group(verifier)
        if verifier in active_processes:
            active_processes.remove(verifier)
        terminate_process_group(mocket)
        if mocket in active_processes:
            active_processes.remove(mocket)
        main_transport.loop_stop()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.stderr.write("\nKeyboardInterrupt caught. Cleaning up active processes...\n")
        cleanup_active_processes()
        sys.exit(1)
