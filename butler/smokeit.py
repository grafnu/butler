import sys
import subprocess
import time
import os

def main():
    if len(sys.argv) < 2:
        print("Usage: bin/smokeit conn_spec")
        sys.exit(1)

    conn_spec = sys.argv[1]
    print("Running setup...")
    setup = subprocess.run(["bin/setup", conn_spec], capture_output=True, text=True)
    if setup.returncode != 0:
        print(f"Setup failed:\n{setup.stderr}")
        sys.exit(1)

    print(setup.stdout)
    print("Starting background components...")

    observe_proc = subprocess.Popen(["bin/observe", conn_spec], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    verifier_proc = subprocess.Popen(["bin/verifier", conn_spec], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    butler_proc = subprocess.Popen(["bin/butler", conn_spec], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    time.sleep(2)
    device_id = "smoke-dev-01"

    print(f"Registering {device_id}...")
    reg = subprocess.run(["bin/register", conn_spec, device_id], capture_output=True, text=True)
    if reg.returncode != 0:
        print(f"Register failed:\n{reg.stderr}")
        cleanup([observe_proc, verifier_proc, butler_proc])
        sys.exit(1)

    mocket_proc = subprocess.Popen(["bin/mocket", conn_spec, device_id], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    time.sleep(3)

    fw_path = "testing/fw_1.1.0.bin"
    os.makedirs(os.path.dirname(fw_path), exist_ok=True)
    with open(fw_path, "w") as f:
        f.write("SMOKE_TEST_FW_V1.1.0")

    print(f"Triggering update for {device_id}...")
    trig = subprocess.run(["bin/trigger", conn_spec, device_id, "1.1.0", fw_path], capture_output=True, text=True)
    if trig.returncode != 0:
        print(f"Trigger failed:\n{trig.stderr}")
        cleanup([observe_proc, verifier_proc, butler_proc, mocket_proc])
        sys.exit(1)

    print("Waiting for update sequence to complete...")
    time.sleep(8)
    cleanup([observe_proc, verifier_proc, butler_proc, mocket_proc])
    print("Smoke test passed")

def cleanup(procs):
    for p in procs:
        p.terminate()
        try:
            p.wait(timeout=2)
        except subprocess.TimeoutExpired:
            p.kill()

if __name__ == '__main__':
    main()
