import subprocess
import os
import sys
import time
import shutil

# This script runs smoke tests cross-implementation pairs to generate logs and the summary matrix
# D is known to be missing a bin directory in this particular iteration, but we include it for graph completeness
impls = ["impl_A", "impl_B", "impl_C", "impl_D"]
conn_spec = "mqtt://localhost/"

pairs = [("impl_A", "impl_B"), ("impl_B", "impl_C"), ("impl_C", "impl_A"), ("impl_A", "impl_D"), ("impl_D", "impl_B")]

with open("impl_test_summary.txt", "w") as f_out:
    for verifier_impl, butler_impl in pairs:
        print(f"Testing {verifier_impl} verifies {butler_impl}")

        test_dir = f"testing_{verifier_impl}_{butler_impl}"
        if os.path.exists(test_dir):
            shutil.rmtree(test_dir)
        os.makedirs(test_dir, exist_ok=True)

        model_file = os.path.join(test_dir, "test_model.json")
        blobs_dir = os.path.join(test_dir, "blobs")

        env = os.environ.copy()
        env["BUTLER_MODEL_FILE"] = os.path.abspath(model_file)
        env["BUTLER_BLOBS_DIR"] = os.path.abspath(blobs_dir)
        env["BUTLER_CONN_SPEC"] = conn_spec

        butler_venv = f"impl/{butler_impl.split("_")[1]}/venv/bin/python3"
        verifier_venv = f"impl/{verifier_impl.split("_")[1]}/venv/bin/python3"

        # In reality, testing across worktrees requires checking them out. Assuming they exist in ./impl/
        if not os.path.exists(f"./impl/{butler_impl.split("_")[1]}/bin/setup") or not os.path.exists(f"./impl/{verifier_impl.split("_")[1]}/bin/setup"):
            print("FAIL (setup script missing)")
            f_out.write(f"{verifier_impl} verifies {butler_impl}: FAIL\n")
            continue

        obs1 = subprocess.Popen([butler_venv, f"./impl/{butler_impl.split("_")[1]}/bin/observe", conn_spec], env=env, stdout=open(f"{butler_impl}.log", "a"), stderr=subprocess.STDOUT)
        obs2 = subprocess.Popen([verifier_venv, f"./impl/{verifier_impl.split("_")[1]}/bin/observe", conn_spec], env=env, stdout=open(f"{verifier_impl}.log", "a"), stderr=subprocess.STDOUT)

        subprocess.run([butler_venv, f"./impl/{butler_impl.split("_")[1]}/bin/setup", conn_spec], env=env, check=True)

        mocket = subprocess.Popen([butler_venv, f"./impl/{butler_impl.split("_")[1]}/bin/mocket", conn_spec, "smoke-reg", "smoke-dev"], env=env)
        time.sleep(2)

        butler = subprocess.Popen([butler_venv, f"./impl/{butler_impl.split("_")[1]}/bin/butler", conn_spec], env=env)
        time.sleep(2)

        verifier = subprocess.Popen([verifier_venv, f"./impl/{verifier_impl.split("_")[1]}/bin/verifier", conn_spec], env=env)
        time.sleep(2)

        try:
            subprocess.run([verifier_venv, f"./impl/{verifier_impl.split("_")[1]}/bin/register", "smoke-reg", "smoke-dev"], env=env, check=True)

            dummy_blob = os.path.join(test_dir, "dummy.bin")
            with open(dummy_blob, "wb") as f:
                f.write(b"NEW_VERSION_CONTENT")

            subprocess.run([verifier_venv, f"./impl/{verifier_impl.split("_")[1]}/bin/trigger", "smoke-reg", "smoke-dev", "1.1.0", os.path.abspath(dummy_blob)], env=env, check=True)

            time.sleep(5)
            import json
            success = False
            for _ in range(30):
                try:
                    with open(model_file, "r") as f:
                        data = json.load(f)
                        s = json.dumps(data)
                        if "1.1.0" in s:
                            success = True
                            break
                except Exception:
                    pass
                time.sleep(1)

            if success:
                f_out.write(f"{verifier_impl} verifies {butler_impl}: PASS\n")
                print("PASS")
            else:
                f_out.write(f"{verifier_impl} verifies {butler_impl}: FAIL\n")
                print("FAIL")

        finally:
            mocket.terminate()
            butler.terminate()
            verifier.terminate()
            obs1.terminate()
            obs2.terminate()
            mocket.wait()
            butler.wait()
            verifier.wait()
            obs1.wait()
            obs2.wait()
