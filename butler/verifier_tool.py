import os
import sys
import json
import time
import uuid
import queue
import threading
import paho.mqtt.client as mqtt
from butler.conn_spec import parse_conn_spec, get_branch_name

# Global state
conn = None
handshake_completed = False
handshake_tx_id = None
handshake_timer = None
handshake_attempts = 0

# Verifier states
# Key: {site_id}/{device_id}/{blob_id}
# Value: state string (unknown, quiescent, pending, success, failure)
verifier_states = {}
verifier_lock = threading.Lock()

# Sequential processing queue
msg_queue = queue.Queue()

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

def publish_validation_event(client, site_id, device_id, blob_id, message, level, status=None, result=None):
    # Topic: [/{prefix}]/uufi/r/{site_id}/d/{device_id}/c/events/validation
    # For self-reporting: device_id = verifier, site_id = unknown
    topic = build_topic(site_id, device_id, "events", "validation", prefix=conn["prefix"])
    
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    tx_id = str(uuid.uuid4())
    
    val_payload = {
        "message": message,
        "level": level
    }
    if device_id: val_payload["device_id"] = device_id
    if blob_id: val_payload["blob_id"] = blob_id
    if status: val_payload["status"] = status
    if result: val_payload["result"] = result
    
    payload = {
        "timestamp": timestamp,
        "version": "1",
        "validation": val_payload
    }
    
    env = {
        "projectId": "vibrant",
        "transactionId": tx_id,
        "publishTime": timestamp,
        "source": conn["principal"],
        "principal": conn["principal"],
        "payload": payload
    }
    
    client.publish(topic, json.dumps(env), qos=1)

def publish_handshake_state(client):
    global handshake_tx_id, handshake_attempts
    if handshake_completed:
        return
        
    handshake_attempts += 1
    if handshake_attempts > 12: # 12 * 5s = 60s
        sys.stderr.write("VERIFIER [ERROR]: Handshake timeout. Terminating...\n")
        os._exit(1)
        
    if not handshake_tx_id:
        handshake_tx_id = str(uuid.uuid4())
        # Log handshake started
        print(f"VERIFIER [INFO]: Handshake started for {conn['principal']}", flush=True)
        
    # Topic: registry-less /uufi/c/state/udmi
    topic = build_topic(None, None, "state", "udmi", prefix=conn["prefix"])
    
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    payload = {
        "timestamp": timestamp,
        "version": "1",
        "setup": {
            "transaction_id": handshake_tx_id
        }
    }
    
    env = {
        "projectId": "vibrant",
        "transactionId": str(uuid.uuid4()),
        "publishTime": timestamp,
        "source": conn["principal"],
        "principal": conn["principal"],
        "payload": payload
    }
    
    client.publish(topic, json.dumps(env), qos=1)
    
    # Schedule next publish in 5 seconds
    t = threading.Timer(5.0, publish_handshake_state, args=(client,))
    t.daemon = True
    t.start()

def process_messages_queue(client):
    while True:
        msg = msg_queue.get()
        try:
            topic_info = parse_topic(msg.topic, conn["prefix"])
            if not topic_info:
                continue
                
            payload_data = json.loads(msg.payload.decode("utf-8"))
            subtype = topic_info["subtype"]
            subfolder = topic_info["subfolder"]
            r_id = topic_info["registry"] or "default"
            d_id = topic_info["device"]
            
            # Handle Handshake Reply
            global handshake_completed
            if subtype == "config" and subfolder == "udmi" and not d_id:
                inner_payload = payload_data.get("payload", {})
                reply_tx_id = inner_payload.get("reply", {}).get("transaction_id") or inner_payload.get("reply", {}).get("transactionId")
                if reply_tx_id == handshake_tx_id and not handshake_completed:
                    handshake_completed = True
                    # Log handshake completed
                    print(f"VERIFIER [INFO]: Handshake completed for {conn['principal']}", flush=True)
                    # Publish self validation
                    publish_validation_event(client, "unknown", "verifier", None, f"Handshake complete for verifier", "INFO", status="quiescent", result="pass")
                continue
                
            # Handle Device State Reports
            if subtype == "state" and d_id:
                inner_payload = payload_data.get("payload", {})
                blobset = inner_payload.get("blobset", {})
                blobs = blobset.get("blobs", {})
                
                # Check for flat fallback
                blobs_to_process = []
                for b_id, b_data in blobs.items():
                    blobs_to_process.append((b_id, b_data))
                if not blobs_to_process:
                    b_id = "system"
                    if "make" in inner_payload or "model" in inner_payload or "version" in inner_payload:
                        blobs_to_process.append((b_id, inner_payload))
                    elif "blobset" in inner_payload:
                        bs = inner_payload["blobset"]
                        if "make" in bs or "model" in bs or "version" in bs:
                            blobs_to_process.append((b_id, bs))
                            
                for b_id, b_data in blobs_to_process:
                    status = b_data.get("status", "quiescent")
                    if not status:
                        status = "quiescent"
                        
                    key = f"{r_id}/{d_id}/{b_id}"
                    
                    with verifier_lock:
                        old_state = verifier_states.get(key, "unknown")
                        new_state = status
                        
                        # "To ensure log clarity, verifiers MUST NOT log a transition if the {new_state} is identical to the {old_state}. This prohibition applies to both the standard output logging and the publication of validation events on the UUFI bus."
                        if old_state == new_state:
                            continue
                            
                        # Rule validation: "Transitions to success or failure MUST only occur from the pending state. A direct transition from quiescent to success or failure is a protocol violation."
                        if old_state == "quiescent" and new_state in ["success", "failure"]:
                            # Protocol violation!
                            msg_err = f"Protocol Violation for {key}: direct transition {old_state} -> {new_state}"
                            print(f"VERIFIER [ERROR]: VALIDATION ERROR: {msg_err}", flush=True)
                            publish_validation_event(client, r_id, d_id, b_id, msg_err, "ERROR", status=new_state, result="fail")
                        else:
                            # Log valid transition
                            print(f"VERIFIER [INFO]: State transition for {r_id}/{d_id}/{b_id}: {old_state} -> {new_state}", flush=True)
                            publish_validation_event(client, r_id, d_id, b_id, f"Valid transition {old_state} -> {new_state}", "INFO", status=new_state, result="pass")
                            
                        verifier_states[key] = new_state
                        
        except Exception as e:
            sys.stderr.write(f"Verifier Error processing message: {e}\n")
        finally:
            msg_queue.task_done()

def on_connect(client, userdata, flags, rc, properties=None):
    sys.stderr.write(f"Verifier Connected with result code {rc}\n")
    # Subscribe to uufi topics
    if conn["prefix"]:
        client.subscribe(f"/{conn['prefix']}/uufi/#", qos=1)
    else:
        client.subscribe("/uufi/#", qos=1)
    sys.stderr.write("Verifier Subscribed to UUFI topic tree\n")
    
    # Start Handshake State Declaration
    publish_handshake_state(client)

def on_message(client, userdata, msg):
    # Enqueue message for sequential processing to avoid race conditions
    msg_queue.put(msg)

def main():
    global conn
    
    try:
        conn = parse_conn_spec(sys.argv, "verifier")
    except Exception as e:
        sys.stderr.write(f"Error parsing connection spec: {e}\n")
        sys.exit(1)
        
    if not os.path.isdir("impl/udmi"):
        sys.stderr.write("Hard Fail: 'impl/udmi' directory is missing!\n")
        sys.exit(1)
        
    client = mqtt.Client()
    client.on_connect = on_connect
    client.on_message = on_message
    
    # Start sequential message consumer thread
    consumer = threading.Thread(target=process_messages_queue, args=(client,), daemon=True)
    consumer.start()
    
    sys.stderr.write(f"Verifier connecting to {conn['host']}:{conn['port']}...\n")
    try:
        client.connect(conn["host"], conn["port"], 60)
    except Exception as e:
        sys.stderr.write(f"Verifier connection failed: {e}\n")
        sys.exit(1)
        
    try:
        client.loop_forever()
    except KeyboardInterrupt:
        sys.stderr.write("Stopping Verifier...\n")
        sys.exit(0)

if __name__ == "__main__":
    main()
