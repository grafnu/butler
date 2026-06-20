import os
import sys
import json
import time
import uuid
import datetime
import threading
import queue
import secrets
import paho.mqtt.client as mqtt

from butler.conn_spec import parse_conn_spec, print_conn_spec

class VerifierObserver:
    def __init__(self, conn_spec):
        self.spec = parse_conn_spec(conn_spec, "verifier")
        
        # Sourcing configuration
        self.prefix = self.spec["prefix"]
        self.host = self.spec["host"]
        self.port = self.spec["port"]
        self.principal = self.spec["principal"]
        
        # Sequenced processing queue to guarantee order of incoming messages
        self.msg_queue = queue.Queue()
        
        # Device tracking states: (site_id, device_id, blob_id) -> state string
        # Possible states: 'unknown', 'quiescent', 'pending', 'success', 'failure'
        self.states = {}
        self.state_lock = threading.Lock()
        
        self.client = None
        self.running = False
        
        # Handshake status
        self.handshake_completed = False
        self.handshake_transaction_id = f"UUFI:verifier-handshake:{uuid.uuid4().hex[:8]}"
        
        # Output initial connectivity details
        print_conn_spec(self.spec)
        
    def build_topic(self, base):
        if self.prefix:
            return f"/{self.prefix}/{base.lstrip('/')}"
        else:
            return f"/{base.lstrip('/')}"
            
    def start(self):
        self.running = True
        
        client_id = f"{self.principal}-{uuid.uuid4().hex[:8]}"
        self.client = mqtt.Client(client_id=client_id, protocol=mqtt.MQTTv311)
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        
        # Connect to broker
        self.client.connect(self.host, self.port, keepalive=60)
        self.client.loop_start()
        
        # Start worker thread for sequential message processing
        self.worker_thread = threading.Thread(target=self.worker_loop, daemon=True)
        self.worker_thread.start()
        
        # Start active handshake routine
        self.handshake_thread = threading.Thread(target=self.handshake_loop, daemon=True)
        self.handshake_thread.start()
        
    def stop(self):
        self.running = False
        if self.client:
            self.client.loop_stop()
            self.client.disconnect()
            
    def get_utc_timestamp(self):
        return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        
    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            # Subscribe to topics
            topics = [
                self.build_topic("uufi/c/config/udmi"), # To receive handshake replies
                self.build_topic("uufi/c/model/cloud"),
                self.build_topic("uufi/r/+/d/+/c/model/cloud"),
                self.build_topic("uufi/r/+/d/+/state/system"),
                self.build_topic("uufi/r/+/d/+/state/blobset"), # Monitor device updates
            ]
            for t in topics:
                client.subscribe(t, qos=1)
        else:
            sys.stderr.write(f"Verifier MQTT connection failed with code {rc}\n")
            sys.stderr.flush()
            
    def on_message(self, client, userdata, msg):
        # Place in thread-safe queue to ensure strict sequential processing order per device
        self.msg_queue.put((msg.topic, msg.payload))
        
    def handshake_loop(self):
        """Active Handshake step 1 republication routine (timeout 60 seconds)."""
        sys.stderr.write(f"VERIFIER [INFO]: Handshake started for {self.principal}\n")
        sys.stderr.flush()
        self.publish_validation_event("unknown", "verifier", None, f"Handshake started for {self.principal}", "INFO", "pass")
        
        start_time = time.time()
        while self.running and not self.handshake_completed:
            if time.time() - start_time >= 60.0:
                sys.stderr.write(f"VERIFIER [ERROR]: VALIDATION ERROR: Handshake timeout after 60 seconds\n")
                sys.stderr.flush()
                # Fail-fast
                os._exit(1)
                
            # Publish Handshake State
            state_topic = self.build_topic("uufi/c/state/udmi")
            state_payload = {
                "version": "1.5.2",
                "timestamp": self.get_utc_timestamp(),
                "setup": {
                    "functions_ver": 9,
                    "transaction_id": self.handshake_transaction_id,
                    "msg_source": self.principal
                }
            }
            state_envelope = {
                "projectId": "vibrant",
                "transactionId": self.handshake_transaction_id,
                "publishTime": self.get_utc_timestamp(),
                "source": self.principal,
                "principal": f"{self.principal}@",
                "nonce": secrets.token_hex(16),
                "payload": state_payload
            }
            self.client.publish(state_topic, json.dumps(state_envelope), qos=1)
            
            # Wait 5 seconds for retry
            time.sleep(5.0)
            
    def worker_loop(self):
        """Processes messages sequentially from the queue."""
        while self.running:
            try:
                topic, payload = self.msg_queue.get(timeout=1.0)
            except queue.Empty:
                continue
                
            try:
                self.process_sequenced_message(topic, payload)
            except Exception as e:
                sys.stderr.write(f"Error processing message: {e}\n")
                sys.stderr.flush()
            finally:
                self.msg_queue.task_done()
                
    def process_sequenced_message(self, topic, raw_payload):
        try:
            envelope = json.loads(raw_payload.decode("utf-8"))
        except Exception as e:
            return
            
        payload = envelope.get("payload", {})
        publish_time = envelope.get("publishTime") or envelope.get("publish_time") or payload.get("timestamp")
        
        # Parse topic parts
        topic_parts = [p for p in topic.split("/") if p]
        if self.prefix and topic_parts and topic_parts[0] == self.prefix:
            topic_parts = topic_parts[1:]
            
        if not topic_parts:
            return
            
        # Determine subType/subFolder based on topic tree
        site_id = None
        device_id = None
        sub_type = None
        sub_folder = None
        
        if len(topic_parts) >= 3 and topic_parts[0] == "uufi" and topic_parts[1] == "c":
            sub_type = topic_parts[2]
            if len(topic_parts) >= 4:
                sub_folder = topic_parts[3]
        elif len(topic_parts) >= 7 and topic_parts[0] == "uufi" and topic_parts[1] == "r" and topic_parts[3] == "d":
            site_id = topic_parts[2]
            device_id = topic_parts[4]
            sub_type = topic_parts[5]
            sub_folder = topic_parts[6]
            
        # Check if Handshake Reply
        if sub_type == "config" and sub_folder == "udmi" and site_id is None and device_id is None:
            # Wrapping or nesting setup/reply inside "udmi" root is strictly prohibited
            if "udmi" in payload:
                sys.stderr.write("Rejecting wrapped/non-compliant handshake reply.\n")
                sys.stderr.flush()
                return
                
            setup = payload.get("setup", {})
            reply = payload.get("reply", {})
            
            # Clients MUST verify this transaction ID (from envelope/payload) and reject any that do not match
            env_tx = envelope.get("transactionId") or envelope.get("transaction_id")
            reply_tx = reply.get("transaction_id") or setup.get("transaction_id")
            
            if env_tx == self.handshake_transaction_id or reply_tx == self.handshake_transaction_id:
                if not self.handshake_completed:
                    self.handshake_completed = True
                    sys.stderr.write(f"VERIFIER [INFO]: Handshake completed for {self.principal}\n")
                    sys.stderr.flush()
                    self.publish_validation_event("unknown", "verifier", None, f"Handshake completed for {self.principal}", "INFO", "pass")
            else:
                sys.stderr.write(f"Rejecting handshake reply due to transaction ID mismatch (expected {self.handshake_transaction_id}, got envelope={env_tx}, payload={reply_tx})\n")
                sys.stderr.flush()
            return
            
        # Check if Cloud Model Update
        if sub_folder == "cloud" and (sub_type == "config" or sub_type == "model" or sub_type == "cloud"):
            if "cloud" in payload:
                return
                
            expected_map = {}
            registries = payload.get("registries") or payload
            
            # Case 1: Nested system.software in a device-scoped model message
            if "system" in payload and isinstance(payload["system"], dict):
                system_data = payload["system"]
                if "software" in system_data and isinstance(system_data["software"], dict):
                    software_data = system_data["software"]
                    if len(topic_parts) >= 5:
                        s_id = topic_parts[2]
                        d_id = topic_parts[4]
                        for blob_id, ver in software_data.items():
                            if ver is not None:
                                expected_map[(s_id, d_id, blob_id)] = str(ver)
                                
            # Case 2: Standard global cloud model registries hierarchy
            if not expected_map:
                for s_id, site_data in registries.items():
                    if not isinstance(site_data, dict) or s_id in ["version", "timestamp"]:
                        continue
                    for d_id, device_data in site_data.items():
                        if not isinstance(device_data, dict):
                            continue
                        if "system" in device_data and isinstance(device_data["system"], dict):
                            sys_data = device_data["system"]
                            if "software" in sys_data and isinstance(sys_data["software"], dict):
                                soft_data = sys_data["software"]
                                for blob_id, ver in soft_data.items():
                                    if ver is not None:
                                        expected_map[(s_id, d_id, blob_id)] = str(ver)
                                        
            with self.state_lock:
                for key, exp_ver in expected_map.items():
                    if key not in self.states:
                        self.states[key] = {
                            "state": "unknown",
                            "expected_version": exp_ver,
                            "actual_version": "0.0.0",
                            "last_publish_time": None
                        }
                    else:
                        self.states[key]["expected_version"] = exp_ver
            return
            
        # Check if Device System State (actual version)
        if sub_type == "state" and sub_folder == "system" and site_id and device_id:
            system_data = payload.get("system", {})
            software_data = system_data.get("software", {})
            
            with self.state_lock:
                for blob_id, info in software_data.items():
                    if info is None:
                        continue
                    if isinstance(info, dict):
                        actual_ver = info.get("version") or info.get("current_version") or "0.0.0"
                    else:
                        actual_ver = str(info)
                        
                    key = (site_id, device_id, blob_id)
                    if key not in self.states:
                        self.states[key] = {
                            "state": "unknown",
                            "expected_version": "0.0.0",
                            "actual_version": actual_ver,
                            "last_publish_time": publish_time
                        }
                    else:
                        if publish_time and self.states[key].get("last_publish_time") and publish_time < self.states[key]["last_publish_time"]:
                            continue
                        self.states[key]["last_publish_time"] = publish_time
                        self.states[key]["actual_version"] = actual_ver
            return
            
        # Check if Device Blobset State report
        if sub_type == "state" and sub_folder == "blobset" and site_id and device_id:
            self.verify_blobset_state(site_id, device_id, payload, publish_time)
            
    def verify_blobset_state(self, site_id, device_id, payload, publish_time=None):
        """Verifies state transition rules for device blobset state reports."""
        blobset_data = payload.get("blobset", {})
        blobs_data = blobset_data.get("blobs", {})
        
        for blob_id, blob_info in blobs_data.items():
            status = blob_info.get("status")
            
            # Map reported status to tracker state
            if status == "pending":
                new_state = "pending"
            elif status == "success":
                new_state = "success"
            elif status in ["failure", "fail"]:
                new_state = "failure"
            else:
                new_state = "quiescent"
                
            key = (site_id, device_id, blob_id)
            with self.state_lock:
                if key not in self.states:
                    self.states[key] = {
                        "state": "unknown",
                        "expected_version": "0.0.0",
                        "actual_version": "0.0.0",
                        "last_publish_time": publish_time
                    }
                else:
                    if publish_time and self.states[key].get("last_publish_time") and publish_time < self.states[key]["last_publish_time"]:
                        sys.stderr.write(f"VERIFIER [INFO]: Ignoring out-of-order state message: {publish_time} < {self.states[key]['last_publish_time']}\n")
                        sys.stderr.flush()
                        continue
                    self.states[key]["last_publish_time"] = publish_time
                    
                old_state = self.states[key]["state"]
                
                # To avoid race conditions, system components MUST NOT interpret pre-update quiescent state reports
                # (sent by the device before it receives the update command) as a termination of the pending transition;
                # the pending tracking state MUST remain active until the device explicitly reports pending or reaches its terminal state.
                if old_state == "pending" and new_state == "quiescent":
                    expected_version = self.states[key]["expected_version"]
                    actual_version = self.states[key]["actual_version"]
                    if expected_version != actual_version:
                        # Ignore pre-update quiescent state reports
                        continue
                        
                if old_state == new_state:
                    # To ensure log clarity, verifiers MUST NOT log a transition if the {new_state} is identical to the {old_state}
                    continue
                    
                # Validate transitions:
                # "Transitions to success or failure MUST only occur from the pending state. A direct transition from quiescent to success or failure is a protocol violation."
                is_violation = False
                if old_state == "quiescent" and new_state in ["success", "failure"]:
                    is_violation = True
                    
                if is_violation:
                    msg = f"Protocol violation: direct transition from {old_state} to {new_state} for {site_id}/{device_id}/{blob_id}"
                    sys.stderr.write(f"VERIFIER [ERROR]: VALIDATION ERROR: {msg}\n")
                    sys.stderr.flush()
                    self.publish_validation_event(site_id, device_id, blob_id, msg, "ERROR", "fail", status=new_state)
                else:
                    msg = f"State transition for {site_id}/{device_id}/{blob_id}: {old_state} -> {new_state}"
                    sys.stderr.write(f"VERIFIER [INFO]: {msg}\n")
                    sys.stderr.flush()
                    self.publish_validation_event(site_id, device_id, blob_id, msg, "INFO", "pass", status=new_state)
                    
                # Update tracking state
                self.states[key]["state"] = new_state
                
    def publish_validation_event(self, site_id, device_id, blob_id, message, level, result, status=None):
        """Publish validation event to [/{prefix}]/uufi/r/{site_id}/d/{device_id}/c/events/validation."""
        if not self.client:
            return
            
        payload = {
            "version": "1.5.2",
            "timestamp": self.get_utc_timestamp(),
            "validation": {
                "message": message,
                "level": level,
                "device_id": device_id,
                "result": result
            }
        }
        if blob_id:
            payload["validation"]["blob_id"] = blob_id
        if status:
            payload["validation"]["status"] = status
            
        envelope = {
            "projectId": "vibrant",
            "transactionId": f"UUFI:verifier-validation:{uuid.uuid4().hex[:8]}",
            "publishTime": self.get_utc_timestamp(),
            "source": self.principal,
            "principal": self.principal,
            "nonce": secrets.token_hex(16),
            "payload": payload
        }
        
        topic = self.build_topic(f"uufi/r/{site_id}/d/{device_id}/c/events/validation")
        self.client.publish(topic, json.dumps(envelope), qos=1)
