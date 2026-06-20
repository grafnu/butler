import os
import sys
import shutil
import signal
import time
import socket
import re
import subprocess
from butler.conn_spec import parse_conn_spec, get_branch_name, get_branch_ports

def configure_dynamic_ports(mqtt_port, etcd_port):
    # 1. Update local_pod.json files
    for path in ["impl/udmi/udmis/etc/local_pod.json", "impl/udmi/etc/local_pod.json"]:
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    content = f.read()
                # Replace port: 8883 with port: mqtt_port (both as string and int)
                content = content.replace('"port": 8883', f'"port": {mqtt_port}')
                with open(path, "w") as f:
                    f.write(content)
                sys.stderr.write(f"Updated {path} MQTT port to {mqtt_port}\n")
            except Exception as e:
                sys.stderr.write(f"Warning: Failed to update MQTT port in {path}: {e}\n")
                
    # 2. Update mosquitto_udmi.conf
    path = "impl/udmi/etc/mosquitto_udmi.conf"
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                content = f.read()
            content = content.replace("listener 8883", f"listener {mqtt_port}")
            with open(path, "w") as f:
                f.write(content)
            sys.stderr.write(f"Updated {path} listener to {mqtt_port}\n")
        except Exception as e:
            sys.stderr.write(f"Warning: Failed to update {path}: {e}\n")

def get_pid_listening_on_port(port):
    try:
        # Check using lsof
        res = subprocess.run(["lsof", "-t", f"-i:{port}"], capture_output=True, text=True)
        pids = res.stdout.strip().split()
        if pids:
            return int(pids[0])
    except Exception:
        pass
    return None

def find_and_write_pids(mqtt_port, etcd_port):
    # Give them a few seconds to fully spin up
    time.sleep(12.0)
    os.makedirs("out", exist_ok=True)
    
    # 1. Find mosquitto
    mqtt_pid = get_pid_listening_on_port(mqtt_port)
    if mqtt_pid:
        with open("out/mosquitto.pid", "w") as f:
            f.write(str(mqtt_pid))
        sys.stderr.write(f"Recorded mosquitto PID: {mqtt_pid}\n")
        
    # 2. Find etcd
    etcd_pid = get_pid_listening_on_port(etcd_port)
    if etcd_pid:
        with open("out/etcd.pid", "w") as f:
            f.write(str(etcd_pid))
        sys.stderr.write(f"Recorded etcd PID: {etcd_pid}\n")
        
    # 3. Find udmis (Java UdmiServicePod)
    try:
        res = subprocess.run(["ps", "ax"], capture_output=True, text=True)
        for line in res.stdout.splitlines():
            if "java" in line and "UdmiServicePod" in line:
                parts = line.strip().split()
                if parts:
                    udmis_pid = int(parts[0])
                    with open("out/udmis.pid", "w") as f:
                        f.write(str(udmis_pid))
                    sys.stderr.write(f"Recorded udmis PID: {udmis_pid}\n")
                    break
    except Exception as e:
        sys.stderr.write(f"Warning: Failed to record udmis PID: {e}\n")

def check_port_open(host, port):
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except Exception:
        return False

def get_process_on_port(port):
    try:
        # Check using lsof
        res = subprocess.run(["lsof", "-t", f"-i:{port}"], capture_output=True, text=True)
        pids = res.stdout.strip().split()
        if pids:
            details = []
            for pid in pids:
                p_res = subprocess.run(["ps", "-p", pid, "-o", "pid,comm,args"], capture_output=True, text=True)
                details.append(p_res.stdout.strip())
            return "\n".join(details)
    except Exception:
        pass
    return None

def teardown_background_services():
    pids = {}
    for service in ["etcd", "mosquitto", "udmis"]:
        pid_file = f"out/{service}.pid"
        if os.path.exists(pid_file):
            try:
                with open(pid_file, "r") as f:
                    pid = int(f.read().strip())
                    pids[service] = (pid, pid_file)
            except Exception as e:
                sys.stderr.write(f"Error reading PID file {pid_file}: {e}\n")
    
    if not pids:
        sys.stderr.write("No active PID files found for teardown.\n")
        return
        
    for service, (pid, pid_file) in pids.items():
        sys.stderr.write(f"Stopping {service} (PID {pid})...\n")
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            sys.stderr.write(f"{service} (PID {pid}) already exited.\n")
            if os.path.exists(pid_file):
                os.remove(pid_file)
            continue
        except Exception as e:
            sys.stderr.write(f"Failed to send SIGTERM to {service}: {e}\n")
            
    # Wait up to 5 seconds
    start_time = time.time()
    while time.time() - start_time < 5.0:
        still_running = False
        for service, (pid, pid_file) in pids.items():
            try:
                os.kill(pid, 0)
                still_running = True
            except ProcessLookupError:
                pass
        if not still_running:
            break
        time.sleep(0.5)
        
    # SIGKILL if still running
    for service, (pid, pid_file) in pids.items():
        try:
            os.kill(pid, 0)
            sys.stderr.write(f"{service} (PID {pid}) still active, forcing SIGKILL...\n")
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        if os.path.exists(pid_file):
            os.remove(pid_file)
            
    sys.stderr.write("Teardown complete.\n")

def main():
    # Detect stop flag
    stop = False
    if "--stop" in sys.argv:
        stop = True
        sys.argv.remove("--stop")
        
    # Detect offline flag
    offline = False
    if "--offline" in sys.argv:
        offline = True
        sys.argv.remove("--offline")
        
    # Parse conn_spec
    try:
        conn = parse_conn_spec(sys.argv, "setup")
    except Exception as e:
        sys.stderr.write(f"Error parsing connection spec: {e}\n")
        sys.exit(1)
        
    # Get branch-specific ports and configure config files
    mqtt_port, etcd_port = get_branch_ports()
    configure_dynamic_ports(mqtt_port, etcd_port)
        
    # Handle stop
    if stop:
        teardown_background_services()
        sys.exit(0)
        
    # Check for hard fail layout requirement
    # "The udmi directory must exist inside the impl/ directory (at impl/udmi/ relative to the workspace root). All tools must verify this filesystem layout on startup and immediately raise a hard error if the impl/udmi directory is not found."
    if not os.path.isdir("impl/udmi"):
        sys.stderr.write("Hard Fail: 'impl/udmi' directory is missing!\n")
        sys.exit(1)
        
    # Automatic Environment & Pip Requirement Validation
    if not offline:
        sys.stderr.write("Validating pip requirements...\n")
        try:
            # We already satisfy the requirements since we installed them manually, but let's make sure
            subprocess.run([sys.executable, "-m", "pip", "install", "-r", "butler/requirements.txt"], check=True)
        except Exception as e:
            sys.stderr.write(f"Warning: Pip requirements installation failed: {e}\n")
            
    # Isolated Site Model Setup
    src_site = "impl/udmi/sites/udmi_site_model"
    dest_site = "testing/udmi_site_model"
    os.makedirs("testing", exist_ok=True)
    if os.path.isdir(src_site):
        sys.stderr.write(f"Copying site model to isolated testing location...\n")
        if os.path.exists(dest_site):
            shutil.rmtree(dest_site)
        shutil.copytree(src_site, dest_site)
    else:
        sys.stderr.write(f"Warning: Source site model {src_site} not found.\n")
        
    # Pre-check port scanning
    ports_to_check = [conn["port"], etcd_port, 1883, 8883, 2379]
    sys.stderr.write("Performing port status pre-checks...\n")
    for p in ports_to_check:
        proc_info = get_process_on_port(p)
        if proc_info:
            sys.stderr.write(f"Port {p} is OCCUPIED by active process(es):\n{proc_info}\n")
            
    # Start broker if not running
    if conn["scheme"] == "mqtt":
        if not check_port_open("localhost", conn["port"]):
            sys.stderr.write(f"Local MQTT broker not running on port {conn['port']}. Starting broker via start_local...\n")
            env = os.environ.copy()
            env["MQTT_PORT"] = str(conn["port"])
            env["ETCD_PORT"] = str(etcd_port)
            try:
                # Spawn in background
                p = subprocess.Popen(["impl/udmi/bin/start_local", "testing/udmi_site_model"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                os.makedirs("out", exist_ok=True)
                with open("out/mosquitto.pid", "w") as f:
                    f.write(str(p.pid))
                sys.stderr.write(f"Successfully started local broker background process (PID {p.pid})\n")
                # Wait for background services to spin up and record their precise PIDs
                find_and_write_pids(conn["port"], etcd_port)
            except Exception as e:
                sys.stderr.write(f"Error starting local broker: {e}\n")
        else:
            sys.stderr.write(f"MQTT broker is already running on port {conn['port']}.\n")
            
    sys.stderr.write("Setup utility execution completed successfully.\n")

if __name__ == "__main__":
    main()
