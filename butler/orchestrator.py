import os
import sys
import json
import time
import uuid
import hashlib
import threading
import paho.mqtt.client as mqtt
from butler.conn_spec import parse_conn_spec, get_branch_name

# Volatile state tracking for devices
# Key: {registry_id}/{device_id}/{blob_id}
# Value: dict containing: expected_version, actual_version, status, tracking_state, pending_since, retry_count, make, model, last_command_payload
device_states = {}
device_states_lock = threading.Lock()

# Deduplication cache
# Key: transaction_id
# Value: timestamp
dedup_cache = {}
dedup_lock = threading.Lock()

# Global config/connection variables
conn = None
TIMEOUT = 60.0 # Default timeout

def get_file_sha256(filepath):
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()

def resolve_package_metadata(make, model, blob_id, version):
    provider = os.environ.get("BUTLER_BLOBSTORE_PROVIDER", "local")
    if provider == "gcs":
        try:
            from google.cloud import storage
            bucket_name = os.environ.get("BUTLER_GCS_BUCKET")
            if not bucket_name:
                sys.stderr.write("GCS Provider Error: BUTLER_GCS_BUCKET is missing\n")
                return None
            client = storage.Client()
            bucket = client.bucket(bucket_name)
            blob_key = f"{make}/{model}/{blob_id}/{version}/bundle.bin"
            blob = bucket.blob(blob_key)
            blob.reload()
            # Try custom metadata or standard x-goog-meta-sha256
            sha256 = blob.metadata.get("sha256") or blob.metadata.get("x-goog-meta-sha256")
            # Time-limited Signed URL valid for 15 minutes
            signed_url = blob.generate_signed_url(expiration=900, method="GET")
            return {"url": signed_url, "sha256": sha256}
        except Exception as e:
            sys.stderr.write(f"GCS metadata resolution failed: {e}\n")
            return None
    else:
        # Default Local Provider
        model_file = os.environ.get("BUTLER_MODEL_FILE", "udmi_blob_store/model.json")
        try:
            with open(model_file, "r") as f:
                catalog = json.load(f)
        except Exception as e:
            sys.stderr.write(f"Error loading catalog file {model_file}: {e}\n")
            return None
            
        try:
            package = catalog[make][model][blob_id][version]
            url = package["url"]
        except KeyError:
            sys.stderr.write(f"Package not found in local catalog: {make}/{model}/{blob_id}/{version}\n")
            return None
            
        if url.startswith("file://"):
            rel_path = url[7:]
            # Resolve relative to project root (current working directory)
            workspace_root = os.getcwd()
            full_path = os.path.join(workspace_root, rel_path)
            try:
                sha256 = get_file_sha256(full_path)
                return {"url": url, "sha256": sha256}
            except Exception as e:
                sys.stderr.write(f"Error reading local file {full_path}: {e}\n")
                return None
        return {"url": url, "sha256": ""}

def parse_topic(topic, prefix):
    t = topic
    if t.startswith("/"):
        t = t[1:]
    if prefix:
        if t.startswith(prefix):
            t = t[len(prefix):].lstrip("/")
            
    if not t.startswith("uufi"):
        return None
        
    parts = t.split("/")
    registry = None
    device = None
    subtype = None
    subfolder = None
    
    i = 1
    while i < len(parts):
        if parts[i] == "r" and i + 1 < len(parts):
            registry = parts[i+1]
            i += 2
        elif parts[i] == "d" and i + 1 < len(parts):
            device = parts[i+1]
            i += 2
        elif parts[i] == "c" and i + 2 < len(parts):
            subtype = parts[i+1]
            subfolder = parts[i+2]
            break
        else:
            i += 1
            
    return {
        "registry": registry,
        "device": device,
        "subtype": subtype,
        "subfolder": subfolder
    }

def build_topic(registry, device, subtype, subfolder, prefix=None):
    topic = ""
    if prefix:
        topic += f"/{prefix}"
    topic += "/uufi"
    if registry:
        topic += f"/r/{registry}"
    if device:
        topic += f"/d/{device}"
    topic += f"/c/{subtype}/{subfolder}"
    return topic

def publish_message(client, topic, payload, envelope_extra=None, exclude_subtype_reg=True):
    # Envelope parameters
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    tx_id = str(uuid.uuid4())
    
    env = {
        "projectId": "vibrant",
        "transactionId": tx_id,
        "publishTime": timestamp,
        "source": conn["principal"],
        "principal": conn["principal"],
        "payload": payload
    }
    if envelope_extra:
        env.update(envelope_extra)
        
    # Standard compliance: "subType Elimination" and "deviceRegistryId Minimization"
    # The subType attribute MUST NOT be included in the envelope of device state or command/config messages
    # The deviceRegistryId MUST NOT be populated in local device-scoped message envelopes where registry is implied by path
    # If exclude_subtype_reg is True, we strip them from envelope if they exist
    if exclude_subtype_reg:
        env.pop("subType", None)
        env.pop("deviceRegistryId", None)
        env.pop("deviceId", None)
        
    client.publish(topic, json.dumps(env), qos=1)

def trigger_update_command(client, key, r_id, d_id, b_id, state):
    meta = resolve_package_metadata(state["make"], state["model"], b_id, state["expected_version"])
    if not meta:
        sys.stderr.write(f"Could not resolve package metadata for {state['make']}/{state['model']}/{b_id}/{state['expected_version']}\n")
        return False
        
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    payload = {
        "timestamp": timestamp,
        "version": "1",
        "blobset": {
            "blobs": {
                b_id: {
                    "url": meta["url"],
                    "sha256": meta["sha256"],
                    "make": state["make"],
                    "model": state["model"],
                    "generation": timestamp
                }
            }
        }
    }
    
    topic = build_topic(r_id, d_id, "config", "blobset", prefix=conn["prefix"])
    
    sys.stderr.write(f"Triggering update command for {key} to version {state['expected_version']}\n")
    publish_message(client, topic, payload)
    
    state["tracking_state"] = "pending"
    state["pending_since"] = time.time()
    state["last_command_payload"] = payload
    return True

def timeout_checker_thread(client):
    while True:
        time.sleep(1.0)
        now = time.time()
        
        # Cleanup deduplication cache
        with dedup_lock:
            expired = [k for k, v in dedup_cache.items() if now - v > 300.0]
            for k in expired:
                dedup_cache.pop(k, None)
                
        with device_states_lock:
            for key, state in list(device_states.items()):
                if state["tracking_state"] == "pending":
                    # Check timeout
                    elapsed = now - state["pending_since"]
                    if elapsed > TIMEOUT:
                        parts = key.split("/")
                        r_id, d_id, b_id = parts[0], parts[1], parts[2]
                        
                        state["retry_count"] += 1
                        if state["retry_count"] <= 3:
                            sys.stderr.write(f"[butler] Timeout waiting for {key} update. Retry {state['retry_count']}/3...\n")
                            # Retry publishing
                            if state["last_command_payload"]:
                                topic = build_topic(r_id, d_id, "config", "blobset", prefix=conn["prefix"])
                                publish_message(client, topic, state["last_command_payload"])
                            state["pending_since"] = now
                        else:
                            sys.stderr.write(f"[butler] Terminal Failure Warning: Retry limit exhausted for {key}.\n")
                            state["tracking_state"] = "failed"
                            # Log terminal failure
                            print(f"[butler] Device {r_id}/{d_id}/{b_id} terminal state failure with version {state['actual_version']}", flush=True)

def on_connect(client, userdata, flags, rc, properties=None):
    sys.stderr.write(f"Connected with result code {rc}\n")
    # Subscribe to uufi topics
    sub_topic = build_topic("+", "+", "#", prefix=conn["prefix"]) # prefix sub tree
    if conn["prefix"]:
        # Also subscribe to registry-less handshake state topic with prefix
        client.subscribe(f"/{conn['prefix']}/uufi/#", qos=1)
    else:
        client.subscribe("/uufi/#", qos=1)
    sys.stderr.write(f"Subscribed to UUFI topic tree\n")
    
    # Expected Version Discovery
    # Publish Model Query to /uufi/c/query/cloud
    query_topic = build_topic(None, None, "query", "cloud", prefix=conn["prefix"])
    publish_message(client, query_topic, {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "version": "1"
    })
    sys.stderr.write("Published Model Query for expected version discovery\n")

def on_message(client, userdata, msg):
    try:
        topic_info = parse_topic(msg.topic, conn["prefix"])
        if not topic_info:
            return
            
        payload_data = json.loads(msg.payload.decode("utf-8"))
        
        # Extract envelope properties
        tx_id = payload_data.get("transactionId") or payload_data.get("transaction_id")
        source = payload_data.get("source")
        principal = payload_data.get("principal")
        inner_payload = payload_data.get("payload", {})
        
        subtype = topic_info["subtype"]
        subfolder = topic_info["subfolder"]
        r_id = topic_info["registry"] or "default"
        d_id = topic_info["device"]
        
        # 1. Deduplication (Applied to Model Update and Command/Config messages, NOT Device State reports)
        if subtype in ["model", "config", "query"]:
            if tx_id:
                with dedup_lock:
                    if tx_id in dedup_cache:
                        # Duplicate message, skip
                        return
                    dedup_cache[tx_id] = time.time()
                    
        # 2. Handshake Protocol Step 1
        if subtype == "state" and subfolder == "udmi" and not d_id:
            # Registry-less client handshake state received on `/uufi/c/state/udmi`
            # Step 2: Config reply confirmation
            client_tx_id = inner_payload.get("setup", {}).get("transaction_id") or inner_payload.get("setup", {}).get("transactionId")
            if client_tx_id:
                sys.stderr.write(f"Received handshake Step 1 from {principal or source} with TX ID {client_tx_id}\n")
                
                # Handshake response configuration
                reply_payload = {
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "version": inner_payload.get("version", "1"),
                    "setup": {
                        "deviceRegistryId": "testing"
                    },
                    "reply": {
                        "transaction_id": client_tx_id
                    }
                }
                
                # Address handshake reply back to client
                # Use principal from received state, fallback to source
                client_principal = principal if principal else source
                env_extra = {
                    "principal": client_principal
                }
                reply_topic = build_topic(None, None, "config", "udmi", prefix=conn["prefix"])
                publish_message(client, reply_topic, reply_payload, envelope_extra=env_extra)
                sys.stderr.write(f"Published handshake Step 2 reply to {client_principal}\n")
            return
            
        # 3. Sourcing Expected Cloud Model
        # Sourced over UUFI config/cloud or model/cloud
        if (subtype == "config" or subtype == "model") and subfolder == "cloud":
            # Cloud model payload contains desired versions
            # It maps registries to devices etc.
            registries = inner_payload.get("registries", {})
            if not registries:
                # Flat format or single registry
                # Standard UDMI model update might have nested device properties directly
                # Let's search recursively for system.software config
                def scan_model_devices(data, current_reg="testing"):
                    for key, val in data.items():
                        if isinstance(val, dict):
                            if "system" in val and "software" in val["system"]:
                                # Found a device! key is device_id
                                software = val["system"]["software"]
                                update_expected_versions(current_reg, key, software)
                            else:
                                scan_model_devices(val, current_reg)
                scan_model_devices(inner_payload)
            else:
                for reg_name, reg_data in registries.items():
                    devices = reg_data.get("devices", {})
                    for dev_name, dev_data in devices.items():
                        software = dev_data.get("system", {}).get("software", {})
                        update_expected_versions(reg_name, dev_name, software)
            return
            
        # 4. Device State Report
        if subtype == "state" and d_id:
            # Authoritative source of actual versions. DO NOT DEDUPLICATE!
            # Extracted: {site_id}/{device_id}/{blob_id}
            # For consistency, use blobs wrapper key
            blobset = inner_payload.get("blobset", {})
            blobs = blobset.get("blobs", {})
            
            # Flat/unnested checking fallback
            blobs_to_process = []
            for b_id, b_data in blobs.items():
                blobs_to_process.append((b_id, b_data))
                
            if not blobs_to_process:
                # Root level check
                b_id = "system"
                if "make" in inner_payload or "model" in inner_payload or "version" in inner_payload:
                    blobs_to_process.append((b_id, inner_payload))
                elif "blobset" in inner_payload:
                    bs = inner_payload["blobset"]
                    if "make" in bs or "model" in bs or "version" in bs:
                        blobs_to_process.append((b_id, bs))
                        
            with device_states_lock:
                for b_id, b_data in blobs_to_process:
                    make = b_data.get("make")
                    model = b_data.get("model")
                    version = b_data.get("version")
                    status = b_data.get("status")
                    lkg = b_data.get("lkg_version")
                    
                    key = f"{r_id}/{d_id}/{b_id}"
                    
                    if key not in device_states:
                        device_states[key] = {
                            "expected_version": "unknown",
                            "actual_version": version,
                            "status": status,
                            "tracking_state": "unknown",
                            "pending_since": 0,
                            "retry_count": 0,
                            "make": make,
                            "model": model,
                            "last_command_payload": None
                        }
                        
                    state = device_states[key]
                    # Update dynamic info
                    state["actual_version"] = version
                    state["status"] = status
                    if make: state["make"] = make
                    if model: state["model"] = model
                    
                    # State Machine logic
                    expected = state["expected_version"]
                    actual = state["actual_version"]
                    current_track = state["tracking_state"]
                    
                    if expected == "unknown":
                        # Expected version not discovered yet, keep unknown
                        continue
                        
                    if actual == expected:
                        if current_track != "quiescent":
                            state["tracking_state"] = "quiescent"
                            state["retry_count"] = 0
                            print(f"[butler] Device {r_id}/{d_id}/{b_id} terminal state quiescent with version {actual}", flush=True)
                    else:
                        # actual != expected
                        if current_track in ["unknown", "quiescent"]:
                            # Initiate active reconciliation
                            state["tracking_state"] = "active"
                            state["retry_count"] = 0
                            success = trigger_update_command(client, key, r_id, d_id, b_id, state)
                        elif current_track == "pending":
                            # Wait for pending transition
                            if status == "pending":
                                # Keep pending, refresh timer
                                state["pending_since"] = time.time()
                            elif status == "success":
                                state["tracking_state"] = "quiescent"
                                state["retry_count"] = 0
                                print(f"[butler] Device {r_id}/{d_id}/{b_id} terminal state success with version {actual}", flush=True)
                            elif status == "failure":
                                state["tracking_state"] = "failed"
                                print(f"[butler] Device {r_id}/{d_id}/{b_id} terminal state failure with version {actual}", flush=True)
                                
    except Exception as e:
        sys.stderr.write(f"Error handling message: {e}\n")

def update_expected_versions(registry_id, device_id, software_data):
    with device_states_lock:
        for b_id, expected_v in software_data.items():
            if not isinstance(expected_v, str):
                continue
            key = f"{registry_id}/{device_id}/{b_id}"
            if key not in device_states:
                device_states[key] = {
                    "expected_version": expected_v,
                    "actual_version": "unknown",
                    "status": None,
                    "tracking_state": "unknown",
                    "pending_since": 0,
                    "retry_count": 0,
                    "make": None,
                    "model": None,
                    "last_command_payload": None
                }
            else:
                state = device_states[key]
                if state["expected_version"] != expected_v:
                    state["expected_version"] = expected_v
                    # Trigger state machine recalculation on next report, or immediately if actual is known
                    if state["actual_version"] != "unknown":
                        if state["actual_version"] == expected_v:
                            state["tracking_state"] = "quiescent"
                            state["retry_count"] = 0
                            print(f"[butler] Device {registry_id}/{device_id}/{b_id} terminal state quiescent with version {expected_v}", flush=True)
                        else:
                            # If already in pending and expected changed, trigger a new update
                            state["tracking_state"] = "active"
                            state["retry_count"] = 0
                            # Wait for device state report to trigger or we can trigger immediately if we have make/model
                            if state["make"] and state["model"]:
                                # Trigger immediately
                                # We need client reference, but client is running.
                                # Let's let the state machine evaluate in the main loop or on next device state report.
                                pass

def main():
    global conn, TIMEOUT
    
    # Parse TIMEOUT from env
    timeout_env = os.environ.get("BUTLER_TIMEOUT")
    if timeout_env:
        try:
            TIMEOUT = float(timeout_env)
        except ValueError:
            pass
            
    # Parse conn_spec
    try:
        conn = parse_conn_spec(sys.argv, "butler")
    except Exception as e:
        sys.stderr.write(f"Error parsing connection spec: {e}\n")
        sys.exit(1)
        
    # Check for hard fail layout requirement
    if not os.path.isdir("impl/udmi"):
        sys.stderr.write("Hard Fail: 'impl/udmi' directory is missing!\n")
        sys.exit(1)
        
    # Set up MQTT client
    client = mqtt.Client()
    client.on_connect = on_connect
    client.on_message = on_message
    
    # Connect
    sys.stderr.write(f"Connecting to broker at {conn['host']}:{conn['port']}...\n")
    try:
        client.connect(conn["host"], conn["port"], 60)
    except Exception as e:
        sys.stderr.write(f"MQTT connection failed: {e}\n")
        sys.exit(1)
        
    # Start timeout checker thread
    checker = threading.Thread(target=timeout_checker_thread, args=(client,), daemon=True)
    checker.start()
    
    # Run loop
    try:
        client.loop_forever()
    except KeyboardInterrupt:
        sys.stderr.write("Stopping Butler...\n")
        sys.exit(0)

if __name__ == "__main__":
    main()
