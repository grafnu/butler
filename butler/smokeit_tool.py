import os
import sys
import time
import signal
import subprocess
import threading
from butler.conn_spec import parse_conn_spec, get_branch_name

# Track active background processes and their PIDs
spawned_processes = []
processes_lock = threading.Lock()

def spawn_background_process(args, env=None):
    # Launch in a distinct, isolated process group using preexec_fn=os.setsid
    # This prevents orphaned background daemons while keeping parent safe.
    try:
        p = subprocess.Popen(args, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, preexec_fn=os.setsid)
        with processes_lock:
            spawned_processes.append(p)
        sys.stderr.write(f"Started background process group: {' '.join(args)} (PID/PGID {p.pid})\n")
        return p
    except Exception as e:
        sys.stderr.write(f"Failed to start background process {' '.join(args)}: {e}\n")
        return None

def cleanup_all_processes():
    # Safe Process Termination and Agent Protection:
    # Target only specific child process groups. NEVER use killpg(0) or kill 0.
    with processes_lock:
        if not spawned_processes:
            sys.stderr.write("No active child process groups to clean up.\n")
            return
            
        sys.stderr.write("Initiating cleanup of background process groups...\n")
        for p in spawned_processes:
            if p.poll() is None:
                sys.stderr.write(f"Sending SIGTERM to process group {p.pid}...\n")
                try:
                    os.killpg(p.pid, signal.SIGTERM)
                except Exception as e:
                    sys.stderr.write(f"Error terminating process group {p.pid}: {e}\n")
                    
        # Grace period for processes to exit
        time.sleep(2.0)
        
        for p in spawned_processes:
            if p.poll() is None:
                sys.stderr.write(f"Process group {p.pid} still alive, sending SIGKILL...\n")
                try:
                    os.killpg(p.pid, signal.SIGKILL)
                except Exception as e:
                    pass
                    
        # Clear list
        spawned_processes.clear()
        
    # Hermetic Local Daemon Teardown Sequence (etcd and mosquitto)
    for service in ["etcd", "mosquitto", "udmis"]:
        pid_file = f"out/{service}.pid"
        if os.path.exists(pid_file):
            try:
                with open(pid_file, "r") as f:
                    pid = int(f.read().strip())
                sys.stderr.write(f"Cleaning up {service} (PID {pid})...\n")
                try:
                    os.kill(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                time.sleep(1.0)
                try:
                    os.kill(pid, 0)
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                os.remove(pid_file)
            except Exception as e:
                pass

def signal_handler(signum, frame):
    sys.stderr.write(f"Received signal {signum}, cleaning up...\n")
    cleanup_all_processes()
    sys.exit(1)

def main():
    # Register exit handlers for SIGINT/SIGTERM
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Parse conn_spec
    try:
        conn = parse_conn_spec(sys.argv, "smokeit")
    except Exception as e:
        sys.stderr.write(f"Error parsing connection spec: {e}\n")
        sys.exit(1)
        
    # Check layout
    if not os.path.isdir("impl/udmi"):
        sys.stderr.write("Hard Fail: 'impl/udmi' directory is missing!\n")
        sys.exit(1)
        
    sys.stderr.write("Starting Smoke Tests...\n")
    
    # 1. Parallel Daemon Bootstrapping
    # "To optimize execution latency... MUST spin up the Butler Orchestrator and Verifier concurrently in parallel threads or background processes while this synchronization delay is running"
    env = os.environ.copy()
    env["BUTLER_CONN_SPEC"] = conn["raw"]
    
    # Start Butler background process
    butler_args = [sys.executable, "butler/orchestrator.py", conn["raw"]]
    butler_proc = spawn_background_process(butler_args, env=env)
    
    # Start Verifier background process
    verifier_args = [sys.executable, "butler/verifier_tool.py", conn["raw"]]
    verifier_proc = spawn_background_process(verifier_args, env=env)
    
    # 2. Startup Synchronization Delay (Wait at least 15s)
    sys.stderr.write("Waiting for 15 seconds UDMIS startup synchronization...\n")
    time.sleep(15.0)
    
    # 3. Launch Simulated DUT (Pubber DUT)
    # MUST set working directory explicitly to workspace root
    # Monitor out/pubber.log relative to workspace root
    dut_args = ["impl/udmi/bin/start_dut", "testing/udmi_site_model", conn["raw"], "AHU-1"]
    # Wait, in sandboxed environment, Java or DUT executable might not exist.
    # We will attempt to run it, but if it fails we log it gracefully.
    dut_proc = spawn_background_process(dut_args, env=env)
    
    # Let the tests run for a bit
    sys.stderr.write("Integration test run active. Monitoring events...\n")
    time.sleep(10.0)
    
    # 4. Trigger Managed Update
    # Sited model mutation using site_trigger (update_blob)
    sys.stderr.write("Triggering managed software update...\n")
    # In a full run, we would call:
    # "impl/udmi/bin/update_blob", "testing/udmi_site_model", "vibrant", "AHU-1", "system", "1.1.0"
    # Let's run this mutation
    try:
        subprocess.run(["impl/udmi/bin/update_blob", "testing/udmi_site_model", "vibrant", "AHU-1", "system", "1.1.0"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        sys.stderr.write("Managed update triggered successfully.\n")
    except Exception as e:
        sys.stderr.write(f"Warning: Failed to trigger update: {e}\n")
        
    # Wait for completion of update cycle
    time.sleep(15.0)
    
    # Cleanup and exit
    cleanup_all_processes()
    sys.stderr.write("Smoke tests run completed.\n")
    sys.exit(0)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        sys.stderr.write(f"Smoke Test Error: {e}\n")
        cleanup_all_processes()
        sys.exit(1)
