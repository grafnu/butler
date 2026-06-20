import sys
import argparse
from butler.conn_spec import parse_conn_spec

def handle_stop():
    import os
    import sys
    import signal
    import time
    
    workspace_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    
    # List of possible PID file paths relative to workspace_root
    pid_paths = [
        "var/udmis.pid",
        "var/mosquitto/mosquitto.pid",
        "var/etcd/etcd.pid",
        "out/udmis.pid",
        "out/mosquitto.pid",
        "out/etcd.pid",
    ]
    
    active_processes = [] # list of tuple: (pid, pid_file_path)
    
    for rel_path in pid_paths:
        pid_file = os.path.join(workspace_root, rel_path)
        if os.path.exists(pid_file):
            try:
                with open(pid_file, "r") as f:
                    content = f.read().strip()
                    if content:
                        pid = int(content)
                        active_processes.append((pid, pid_file))
            except Exception as e:
                sys.stderr.write(f"Error reading PID file {pid_file}: {e}\n")
                
    if not active_processes:
        print("No active background services found (no PID files found).")
        return
        
    print(f"Stopping background services: {[p[0] for p in active_processes]}")
    
    # Send SIGTERM to all PIDs
    for pid, pid_file in active_processes:
        try:
            print(f"Sending SIGTERM to PID {pid} (from {os.path.basename(pid_file)})")
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except Exception as e:
            sys.stderr.write(f"Error sending SIGTERM to PID {pid}: {e}\n")
            
    # Wait for up to 5 seconds for processes to exit
    start_time = time.time()
    while (time.time() - start_time) < 5.0:
        still_running = False
        for pid, _ in active_processes:
            try:
                os.kill(pid, 0)
                still_running = True
                break
            except ProcessLookupError:
                pass
        if not still_running:
            break
        time.sleep(0.1)
        
    # Send SIGKILL to any process still alive, and delete PID files
    for pid, pid_file in active_processes:
        still_alive = False
        try:
            os.kill(pid, 0)
            still_alive = True
        except ProcessLookupError:
            pass
            
        if still_alive:
            try:
                print(f"PID {pid} still active after 5s grace period. Sending SIGKILL.")
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except Exception as e:
                sys.stderr.write(f"Error sending SIGKILL to PID {pid}: {e}\n")
                
        # Delete the pid file
        try:
            if os.path.exists(pid_file):
                os.remove(pid_file)
                print(f"Deleted PID file: {pid_file}")
        except Exception as e:
            sys.stderr.write(f"Error deleting PID file {pid_file}: {e}\n")
            
    print("Background services stopped.")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("pos_conn_spec", nargs="?", help="Connection spec URL")
    parser.add_argument("--conn_spec", help="Connection spec URL")
    parser.add_argument("--offline", action="store_true", help="Offline mode")
    parser.add_argument("--stop", action="store_true", help="Stop background services")
    args, unknown = parser.parse_known_args()

    if args.stop:
        handle_stop()
        return

    conn_str = args.conn_spec or args.pos_conn_spec
    conn_spec = parse_conn_spec(conn_str, differentiator="setup")
    sys.stderr.write(f"{conn_spec.format_conn_spec()}\n")
    
    if conn_spec.protocol == "mqtt":
        import paho.mqtt.client as mqtt
        import subprocess
        import time
        import secrets
        host = conn_spec.host
        port = conn_spec.port or 1883
        
        # Automatic Port Status Pre-Check
        def perform_port_precheck(host, port):
            import socket
            import subprocess
            import sys
            
            ports_to_check = [port]
            if port != 1883:
                ports_to_check.append(1883)
            if port != 8883:
                ports_to_check.append(8883)
            ports_to_check.append(2379)
            ports_to_check.append(port + 1)
            
            occupied_ports = []
            for p in ports_to_check:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.settimeout(0.1)
                    try:
                        s.connect(("127.0.0.1", p))
                        occupied_ports.append(p)
                    except Exception:
                        pass
            
            if occupied_ports:
                sys.stderr.write(f"Port Pre-check: Detected occupied ports: {occupied_ports}\n")
                for p in occupied_ports:
                    try:
                        res = subprocess.run(["lsof", "-t", f"-i:{p}"], capture_output=True, text=True)
                        pids = res.stdout.strip().split()
                        if pids:
                            sys.stderr.write(f"Port {p} occupied by PID(s): {', '.join(pids)}\n")
                            for pid in pids:
                                try:
                                    with open(f"/proc/{pid}/cmdline", "r") as f:
                                        cmd = f.read().replace('\x00', ' ')
                                    sys.stderr.write(f"  PID {pid} command: {cmd}\n")
                                except Exception:
                                    res2 = subprocess.run(["ps", "-p", pid, "-o", "command="], capture_output=True, text=True)
                                    sys.stderr.write(f"  PID {pid} command: {res2.stdout.strip()}\n")
                        else:
                            res = subprocess.run(["ss", "-lntp"], capture_output=True, text=True)
                            for line in res.stdout.splitlines():
                                if f":{p} " in line:
                                    sys.stderr.write(f"Port {p} info from ss: {line}\n")
                    except Exception as e:
                        sys.stderr.write(f"Error querying process info for port {p}: {e}\n")

        perform_port_precheck(host, port)

        def check_mqtt():
            client_id = f"setup-{secrets.token_hex(4)}"
            if conn_spec.prefix:
                client_id = f"{conn_spec.prefix.replace('/', '-')}-{client_id}"
            try:
                # Try for paho-mqtt 2.0.0+ API
                client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
                is_v2 = True
            except AttributeError:
                is_v2 = False
                # Fallback for older versions
                try:
                    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id=client_id)
                except AttributeError:
                    client = mqtt.Client(client_id=client_id)
            
            connected_status = [None]
            
            def on_connect_v2(cl, ud, flags, rc, properties=None):
                connected_status[0] = (rc == 0)
                
            def on_connect_v1(cl, ud, flags, rc):
                connected_status[0] = (rc == 0)
                
            if is_v2:
                client.on_connect = on_connect_v2
            else:
                client.on_connect = on_connect_v1

            if port != 1883:
                client.username_pw_set("rocket", "monkey")
            elif conn_spec.username:
                password = getattr(conn_spec, 'password', None)
                client.username_pw_set(conn_spec.username, password)
            
            # Check if certs exist and port is not 1883
            if port != 1883:
                import os
                workspace_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                # Always prefer sibling/peer udmi/var/mosquitto/certs first
                peer_udmi_certs = os.path.join(workspace_root, "..", "udmi", "var", "mosquitto", "certs")
                ca_file = os.path.join(peer_udmi_certs, "ca.crt")
                cert_file = os.path.join(peer_udmi_certs, "rsa_private.crt")
                key_file = os.path.join(peer_udmi_certs, "rsa_private.pem")
                
                if not (os.path.exists(ca_file) and os.path.exists(cert_file) and os.path.exists(key_file)):
                    # Try local impl/udmi/var/mosquitto/certs
                    symlink_certs = os.path.join(workspace_root, "impl", "udmi", "var", "mosquitto", "certs")
                    ca_file = os.path.join(symlink_certs, "ca.crt")
                    cert_file = os.path.join(symlink_certs, "rsa_private.crt")
                    key_file = os.path.join(symlink_certs, "rsa_private.pem")
                    
                    if not (os.path.exists(ca_file) and os.path.exists(cert_file) and os.path.exists(key_file)):
                        # Fallback to local workspace var/mosquitto/certs
                        ca_file = os.path.join(workspace_root, "var", "mosquitto", "certs", "ca.crt")
                        cert_file = os.path.join(workspace_root, "var", "mosquitto", "certs", "rsa_private.crt")
                        key_file = os.path.join(workspace_root, "var", "mosquitto", "certs", "rsa_private.pem")
                
                if os.path.exists(ca_file) and os.path.exists(cert_file) and os.path.exists(key_file):
                    client.tls_set(ca_certs=ca_file, certfile=cert_file, keyfile=key_file)
                    client.tls_insecure_set(True)

            try:
                client.connect(host, port, 5)
                client.loop_start()
                # Wait up to 2 seconds for on_connect to fire
                start_wait = time.time()
                while connected_status[0] is None and (time.time() - start_wait) < 2.0:
                    time.sleep(0.05)
                client.loop_stop()
                client.disconnect()
                return connected_status[0] is True
            except (ConnectionRefusedError, OSError):
                return False
            except Exception as e:
                import traceback
                traceback.print_exc()
                return False

        def add_acl():
            if host == "localhost" and conn_spec.prefix:
                print(f"Adding Mosquitto ACL rules for prefix: {conn_spec.prefix}...")
                try:
                    import os
                    workspace_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                    # Always prefer sibling/peer udmi/var/mosquitto/certs first
                    peer_udmi_certs = os.path.join(workspace_root, "..", "udmi", "var", "mosquitto", "certs")
                    ca_file = os.path.join(peer_udmi_certs, "ca.crt")
                    cert_file = os.path.join(peer_udmi_certs, "rsa_private.crt")
                    key_file = os.path.join(peer_udmi_certs, "rsa_private.pem")
                    
                    if not (os.path.exists(ca_file) and os.path.exists(cert_file) and os.path.exists(key_file)):
                        # Try local impl/udmi/var/mosquitto/certs
                        symlink_certs = os.path.join(workspace_root, "impl", "udmi", "var", "mosquitto", "certs")
                        ca_file = os.path.join(symlink_certs, "ca.crt")
                        cert_file = os.path.join(symlink_certs, "rsa_private.crt")
                        key_file = os.path.join(symlink_certs, "rsa_private.pem")
                        
                        if not (os.path.exists(ca_file) and os.path.exists(cert_file) and os.path.exists(key_file)):
                            # Fallback to local workspace var/mosquitto/certs
                            ca_file = os.path.join(workspace_root, "var", "mosquitto", "certs", "ca.crt")
                            cert_file = os.path.join(workspace_root, "var", "mosquitto", "certs", "rsa_private.crt")
                            key_file = os.path.join(workspace_root, "var", "mosquitto", "certs", "rsa_private.pem")
                    
                    prefix_pattern = f"/{conn_spec.prefix}/#"
                    for role in ["service", "uufi"]:
                        for acl_type in ["subscribePattern", "publishClientSend"]:
                            cmd = [
                                "mosquitto_ctrl",
                                "-h", "localhost",
                                "-p", str(port),
                                "-u", "scrumptious",
                                "-P", "aardvark",
                                "--cafile", ca_file,
                                "--cert", cert_file,
                                "--key", key_file,
                                "dynsec",
                                "addRoleACL",
                                role,
                                acl_type,
                                prefix_pattern,
                                "allow"
                            ]
                            res = subprocess.run(cmd, capture_output=True, text=True)
                            if res.returncode != 0:
                                print(f"mosquitto_ctrl failed for {role}/{acl_type}: {res.stderr.strip()}", file=sys.stderr)
                    print(f"Successfully added ACL rules for /{conn_spec.prefix}/#")
                except Exception as e:
                    print(f"Warning: Failed to add ACL rules for prefix {conn_spec.prefix}: {e}")

        print(f"Checking connectivity to MQTT broker at {host}:{port}...")
        if check_mqtt():
            print("Successfully connected to MQTT broker.")
            add_acl()
        elif host == "localhost":
            print("Failed to connect. Attempting to start local mosquitto server...")
            try:
                # ASSUMPTION: Start the local broker by invoking the local UDMI tool (udmi/bin/start_local)
                # We locate the start_local script relative to this file's workspace root.
                # We specify the standard test site 'udmi/tests/sites/basic' and the custom port configuration 'localhost:{port}'.
                import os
                workspace_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                start_local_script = os.path.join(workspace_root, "impl", "udmi", "bin", "start_local")
                site_path = os.path.join(workspace_root, "impl", "udmi", "tests", "sites", "basic")
                project_spec = f"//mqtt/localhost:{port}"
                
                # Run the start_local script to spin up mosquitto and other local infrastructure in an isolated process group
                env = os.environ.copy()
                env["PATH"] = f"{workspace_root}/tmp/bin:{env.get('PATH', '')}"
                env["LD_LIBRARY_PATH"] = f"{workspace_root}/tmp/bin:{env.get('LD_LIBRARY_PATH', '')}"
                subprocess.Popen([start_local_script, site_path, project_spec], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, preexec_fn=os.setsid, env=env)
                
                # Wait for up to 20 seconds for the broker to start
                print("Waiting for local mosquitto to start and accept connections...")
                success = False
                for attempt in range(20):
                    time.sleep(1)
                    if check_mqtt():
                        success = True
                        break
                if success:
                    print("Successfully started and connected to local mosquitto.")
                    add_acl()
                else:
                    print("Failed to start mosquitto or it is still not accessible.")
                    sys.exit(1)
            except Exception as e:
                print(f"Error starting mosquitto via start_local: {e}")
                sys.exit(1)
        else:
            print(f"Failed to connect to remote MQTT broker at {host}:{port}.")
            sys.exit(1)

    elif conn_spec.protocol == "pubsub":
        from google.cloud import pubsub_v1
        from google.api_core import exceptions
        
        publisher = pubsub_v1.PublisherClient()
        subscriber = pubsub_v1.SubscriberClient()
        
        topic_path = publisher.topic_path(conn_spec.project_id, conn_spec.root_topic)
        sub_path = subscriber.subscription_path(conn_spec.project_id, conn_spec.subscription)
        
        print(f"Checking PubSub topic: {topic_path}")
        try:
            publisher.get_topic(topic=topic_path)
            print(f"Topic {conn_spec.root_topic} exists.")
        except exceptions.NotFound:
            print(f"FAIL: Topic {conn_spec.root_topic} not found. PubSub resources must be pre-configured.")
            sys.exit(1)
        except Exception as e:
            print(f"Error checking topic: {e}")
            sys.exit(1)
            
        print(f"Checking PubSub subscription: {sub_path}")
        try:
            subscriber.get_subscription(subscription=sub_path)
            print(f"Subscription {conn_spec.subscription} exists.")
        except exceptions.NotFound:
            print(f"FAIL: Subscription {conn_spec.subscription} not found. PubSub resources must be pre-configured.")
            sys.exit(1)
        except Exception as e:
            print(f"Error checking subscription: {e}")
            sys.exit(1)
    
    print("Bus setup complete.")

if __name__ == "__main__":
    main()
