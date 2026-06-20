import os
import sys
import json
import time
import uuid
import datetime
import threading
import secrets
import paho.mqtt.client as mqtt

from butler.conn_spec import parse_conn_spec, print_conn_spec
from butler.blobstore import get_blobstore_provider

class ButlerOrchestrator:
    def __init__(self, conn_spec, force_flag=False):
        self.spec = parse_conn_spec(conn_spec, "butler")
        self.force_flag = force_flag
        
        # Sourcing configuration
        self.prefix = self.spec["prefix"]
        self.host = self.spec["host"]
        self.port = self.spec["port"]
        self.principal = self.spec["principal"]
        
        # State tracking: (site_id, device_id, blob_id) -> state dict
        # State dict contains:
        #   expected_version: str
        #   actual_version: str
        #   status: str
        #   state_machine_state: str ('unknown', 'quiescent', 'active', 'pending', 'failed')
        #   retry_count: int
        #   last_sent_time: float
        #   make: str
        #   model: str
        #   lkg_version: str
        #   last_command_payload: dict
        self.states = {}
        self.state_lock = threading.Lock()
        
        # Deduplication cache: transaction_id -> timestamp of receipt
        self.dedup_cache = {}
        self.dedup_lock = threading.Lock()
        
        self.timeout = float(os.environ.get("BUTLER_TIMEOUT", "60"))
        self.blobstore = get_blobstore_provider()
        
        self.client = None
        self.running = False
        
        # Output initial connectivity details
        print_conn_spec(self.spec)
        
    def build_topic(self, base):
        """Build a topic with the optional prefix."""
        if self.prefix:
            return f"/{self.prefix}/{base.lstrip('/')}"
        else:
            return f"/{base.lstrip('/')}"
            
    def start(self):
        self.running = True
        
        # Setup MQTT client
        client_id = f"{self.principal}-{uuid.uuid4().hex[:8]}"
        self.client = mqtt.Client(client_id=client_id, protocol=mqtt.MQTTv311)
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        
        # Connect to broker
        self.client.connect(self.host, self.port, keepalive=60)
        self.client.loop_start()
        
        # Start background timeout and deduplication cleaner threads
        self.timeout_thread = threading.Thread(target=self.timeout_loop, daemon=True)
        self.timeout_thread.start()
        
        # Wait a small amount then publish initial Model Query to discover expected configurations
        time.sleep(1.0)
        self.publish_model_query()
        
    def stop(self):
        self.running = False
        if self.client:
            self.client.loop_stop()
            self.client.disconnect()
            
    def publish_model_query(self):
        """Publish Model Query on [/{prefix}]/uufi/c/query/cloud."""
        topic = self.build_topic("uufi/c/query/cloud")
        payload = {
            "version": "1.5.2",
            "timestamp": self.get_utc_timestamp(),
            "generation": self.get_utc_timestamp(),
            "depth": "entries"
        }
        envelope = {
            "projectId": "vibrant",
            "transactionId": f"UUFI:butler-query:{uuid.uuid4().hex[:8]}",
            "publishTime": self.get_utc_timestamp(),
            "source": self.principal,
            "principal": self.principal,
            "nonce": secrets.token_hex(16),
            "payload": payload
        }
        self.client.publish(topic, json.dumps(envelope), qos=1)
        sys.stderr.write(f"Published model query on {topic}\n")
        sys.stderr.flush()
        
    def get_utc_timestamp(self):
        return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        
    def check_deduplicate(self, transaction_id):
        """Returns True if message is a duplicate, otherwise inserts and returns False."""
        if not transaction_id:
            return False
        with self.dedup_lock:
            now = time.time()
            # Clean old entries (> 5 minutes)
            self.dedup_cache = {tid: ts for tid, ts in self.dedup_cache.items() if now - ts < 300}
            if transaction_id in self.dedup_cache:
                return True
            self.dedup_cache[transaction_id] = now
            return False
            
    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            # Subscribe to all relevant topics
            topics = [
                # Handshake states from Clients
                self.build_topic("uufi/c/state/udmi"),
                # Expected cloud models (only model/cloud is allowed, config/cloud is strictly prohibited)
                self.build_topic("uufi/c/model/cloud"),
                self.build_topic("uufi/r/+/d/+/c/model/cloud"),
                # Device State reports
                self.build_topic("uufi/r/+/d/+/state/system"),
                self.build_topic("uufi/r/+/d/+/state/blobset"),
            ]
            for t in topics:
                client.subscribe(t, qos=1)
        else:
            sys.stderr.write(f"MQTT Connection failed with code {rc}\n")
            sys.stderr.flush()
            
    def on_message(self, client, userdata, msg):
        # Run callback processing in a background thread to keep MQTT event loop responsive
        threading.Thread(target=self.process_message, args=(msg.topic, msg.payload), daemon=True).start()
        
    def process_message(self, topic, raw_payload):
        try:
            envelope = json.loads(raw_payload.decode("utf-8"))
        except Exception as e:
            sys.stderr.write(f"Failed to parse JSON from topic {topic}: {e}\n")
            sys.stderr.flush()
            return
            
        # Extract envelope fields
        transaction_id = envelope.get("transactionId") or envelope.get("transaction_id")
        source = envelope.get("source")
        principal = envelope.get("principal")
        payload = envelope.get("payload", {})
        publish_time = envelope.get("publishTime") or envelope.get("publish_time") or payload.get("timestamp")
        
        # Topic parsing
        topic_parts = [p for p in topic.split("/") if p]
        # Strip prefix if present
        if self.prefix and topic_parts and topic_parts[0] == self.prefix:
            topic_parts = topic_parts[1:]
            
        if not topic_parts:
            return
            
        # Determine subType/subFolder based on topic tree
        # Topics format: uufi/c/state/udmi, uufi/r/{site_id}/d/{device_id}/state/{subFolder}
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
            
        # 1. Handle Handshake state from Client
        # Topic: [/{prefix}]/uufi/c/state/udmi
        if sub_type == "state" and sub_folder == "udmi":
            self.handle_handshake(envelope, payload)
            return
            
        # Deduplicate incoming Model Update and Command/Config messages
        # "MUST NOT discard or skip processing of incoming Device State reports"
        is_state_report = (sub_type == "state")
        if not is_state_report:
            if self.check_deduplicate(transaction_id):
                sys.stderr.write(f"Ignoring duplicate transaction {transaction_id}\n")
                sys.stderr.flush()
                return
                
        # 2. Handle Cloud Model Updates / Replies
        if sub_folder == "cloud" and (sub_type == "config" or sub_type == "model" or sub_type == "cloud"):
            self.handle_cloud_model(payload, site_id, device_id)
            return
            
        # 3. Handle Device State Updates
        if is_state_report and site_id and device_id:
            if sub_folder == "system":
                self.handle_device_system_state(site_id, device_id, payload, publish_time)
            elif sub_folder == "blobset":
                self.handle_device_blobset_state(site_id, device_id, payload, publish_time)
                
    def handle_handshake(self, envelope, payload):
        """Respond to Handshake state report with config reply on [/{prefix}]/uufi/c/config/udmi."""
        # Wrapping or nesting these blocks inside a "udmi" root sub-object is strictly prohibited and MUST be rejected as non-compliant
        if "udmi" in payload:
            sys.stderr.write("Rejecting wrapped/non-compliant handshake request.\n")
            sys.stderr.flush()
            return
            
        # Extract fields
        source = envelope.get("source")
        principal = envelope.get("principal")
        
        setup = payload.get("setup", {})
        functions_ver = setup.get("functions_ver", 9)
        transaction_id = setup.get("transaction_id") or envelope.get("transactionId") or envelope.get("transaction_id")
        msg_source = setup.get("msg_source") or source
        
        # Standard compliant target principal resolution
        # Use received principal first, fallback to source
        target_principal = principal or source or "unknown"
        if target_principal.endswith("@"):
            target_principal = target_principal[:-1]
            
        reply_payload = {
            "version": "1.5.2",
            "timestamp": self.get_utc_timestamp(),
            "setup": {
                "functions_ver": functions_ver,
                "transaction_id": transaction_id,
                "msg_source": msg_source,
                "deviceRegistryId": "default"
            },
            "reply": {
                "transaction_id": transaction_id,
                "status": "success"
            }
        }
        
        # To support request-response correlation, reply envelope MUST include exact transaction_id from request
        reply_envelope = {
            "projectId": "vibrant",
            "transactionId": transaction_id,
            "transaction_id": transaction_id,
            "publishTime": self.get_utc_timestamp(),
            "source": self.principal,
            "principal": target_principal,
            "nonce": secrets.token_hex(16),
            "payload": reply_payload
        }
        
        reply_topic = self.build_topic("uufi/c/config/udmi")
        self.client.publish(reply_topic, json.dumps(reply_envelope), qos=1)
        sys.stderr.write(f"Sent Handshake Reply to {target_principal} on {reply_topic}\n")
        sys.stderr.flush()
        
    def handle_cloud_model(self, payload, topic_site_id=None, topic_device_id=None):
        """Extract expected versions from cloud model and update expectations."""
        # Wrapping or nesting the update payload inside a "cloud" root sub-object is strictly prohibited and MUST be rejected
        if "cloud" in payload:
            sys.stderr.write("Rejecting wrapped/non-compliant cloud model update.\n")
            sys.stderr.flush()
            return
            
        expected_map = {}
        
        # Handle "registries" nesting or directly flat (cloud model updates MUST have registries directly at payload root)
        registries = payload.get("registries") or payload
        
        # Case 1: Nested system.software in a device-scoped model message
        if "system" in payload and isinstance(payload["system"], dict):
            system_data = payload["system"]
            if "software" in system_data and isinstance(system_data["software"], dict):
                software_data = system_data["software"]
                if topic_site_id and topic_device_id:
                    for blob_id, ver in software_data.items():
                        if ver is not None:
                            expected_map[(topic_site_id, topic_device_id, blob_id)] = str(ver)
                            
        # Case 2: Standard global cloud model registries hierarchy
        if not expected_map:
            for site_id, site_data in registries.items():
                if not isinstance(site_data, dict) or site_id in ["version", "timestamp"]:
                    continue
                for device_id, device_data in site_data.items():
                    if not isinstance(device_data, dict):
                        continue
                    if "system" in device_data and isinstance(device_data["system"], dict):
                        sys_data = device_data["system"]
                        if "software" in sys_data and isinstance(sys_data["software"], dict):
                            soft_data = sys_data["software"]
                            for blob_id, ver in soft_data.items():
                                if ver is not None:
                                    expected_map[(site_id, device_id, blob_id)] = str(ver)
                                    
        with self.state_lock:
            for key, exp_ver in expected_map.items():
                site_id, device_id, blob_id = key
                if key not in self.states:
                    self.states[key] = {
                        "expected_version": exp_ver,
                        "actual_version": "0.0.0",
                        "status": None,
                        "state_machine_state": "unknown",
                        "retry_count": 0,
                        "last_sent_time": 0.0,
                        "make": "unknown",
                        "model": "unknown",
                        "lkg_version": "0.0.0",
                        "last_command_payload": None,
                        "last_publish_time": None
                    }
                else:
                    old_exp = self.states[key]["expected_version"]
                    if old_exp != exp_ver:
                        self.states[key]["expected_version"] = exp_ver
                        # If expectation changed, reset retry parameters
                        self.states[key]["retry_count"] = 0
                        self.states[key]["last_sent_time"] = 0.0
                        
                # Evaluate transition immediately on expectation change
                self.reconcile_device(site_id, device_id, blob_id)
                
    def handle_device_system_state(self, site_id, device_id, payload, publish_time=None):
        """Parse system.software from state/system message."""
        system_data = payload.get("system", {})
        software_data = system_data.get("software", {})
        
        with self.state_lock:
            for blob_id, info in software_data.items():
                if info is None:
                    continue
                
                # Sourcing actual versions and make/model exclusively from system.software.<blob_id> nesting
                progress_reset = False
                if isinstance(info, dict):
                    actual_ver = info.get("version") or info.get("current_version") or "0.0.0"
                    make = info.get("make") or "unknown"
                    model = info.get("model") or "unknown"
                    
                    # Detect dynamic indication of active progress to reset the timer
                    progress_keys = ["progress", "percentage", "download_percentage", "block_count", "downloaded"]
                    if any(k in info and info[k] is not None for k in progress_keys):
                        progress_reset = True
                else:
                    actual_ver = str(info)
                    make = "unknown"
                    model = "unknown"
                
                key = (site_id, device_id, blob_id)
                if key not in self.states:
                    self.states[key] = {
                        "expected_version": "0.0.0",
                        "actual_version": actual_ver,
                        "status": None,
                        "state_machine_state": "unknown",
                        "retry_count": 0,
                        "last_sent_time": 0.0,
                        "make": make,
                        "model": model,
                        "lkg_version": "0.0.0",
                        "last_command_payload": None,
                        "last_publish_time": publish_time
                    }
                else:
                    if publish_time and self.states[key].get("last_publish_time") and publish_time < self.states[key]["last_publish_time"]:
                        sys.stderr.write(f"Ignoring out-of-order system state message: {publish_time} < {self.states[key]['last_publish_time']}\n")
                        sys.stderr.flush()
                        continue
                    
                    self.states[key]["last_publish_time"] = publish_time
                    self.states[key]["actual_version"] = actual_ver
                    if make != "unknown":
                        self.states[key]["make"] = make
                    if model != "unknown":
                        self.states[key]["model"] = model
                        
                    # Reset timeout timer on progress update
                    if progress_reset and self.states[key]["state_machine_state"] == "pending":
                        self.states[key]["last_sent_time"] = time.time()
                        self.states[key]["retry_count"] = 0
                        sys.stderr.write(f"Timeout timer reset on system.software progress for {site_id}/{device_id}/{blob_id}\n")
                        sys.stderr.flush()
                    
                self.reconcile_device(site_id, device_id, blob_id)
                
    def handle_device_blobset_state(self, site_id, device_id, payload, publish_time=None):
        """Parse blobset.blobs from state/blobset message."""
        blobset_data = payload.get("blobset", {})
        blobs_data = blobset_data.get("blobs", {})
        
        with self.state_lock:
            for blob_id, blob_info in blobs_data.items():
                # Sourcing actual versions, make, and model from blobset payloads is strictly prohibited!
                status = blob_info.get("status")
                lkg_version = blob_info.get("lkg_version") or "0.0.0"
                
                # Detect progress in blobset state to reset timer
                progress_reset = False
                progress_keys = ["progress", "percentage", "download_percentage", "block_count", "downloaded"]
                if any(k in blob_info and blob_info[k] is not None for k in progress_keys):
                    progress_reset = True
                
                key = (site_id, device_id, blob_id)
                if key not in self.states:
                    self.states[key] = {
                        "expected_version": "0.0.0",
                        "actual_version": "0.0.0",  # Sourcing from blobset is prohibited
                        "status": status,
                        "state_machine_state": "unknown",
                        "retry_count": 0,
                        "last_sent_time": 0.0,
                        "make": "unknown",          # Sourcing from blobset is prohibited
                        "model": "unknown",         # Sourcing from blobset is prohibited
                        "lkg_version": lkg_version,
                        "last_command_payload": None,
                        "last_publish_time": publish_time
                    }
                else:
                    if publish_time and self.states[key].get("last_publish_time") and publish_time < self.states[key]["last_publish_time"]:
                        sys.stderr.write(f"Ignoring out-of-order blobset state message: {publish_time} < {self.states[key]['last_publish_time']}\n")
                        sys.stderr.flush()
                        continue
                    
                    self.states[key]["last_publish_time"] = publish_time
                    # Update status and lkg_version, but NOT actual_version, make, or model
                    self.states[key]["status"] = status
                    self.states[key]["lkg_version"] = lkg_version
                    
                    # Reset timeout timer on progress update
                    if progress_reset and self.states[key]["state_machine_state"] == "pending":
                        self.states[key]["last_sent_time"] = time.time()
                        self.states[key]["retry_count"] = 0
                        sys.stderr.write(f"Timeout timer reset on blobset progress for {site_id}/{device_id}/{blob_id}\n")
                        sys.stderr.flush()
                        
                self.reconcile_device(site_id, device_id, blob_id)
                
    def reconcile_device(self, site_id, device_id, blob_id):
        """Re-evaluate the tracking state machine and trigger updates if needed."""
        key = (site_id, device_id, blob_id)
        state_dict = self.states[key]
        
        expected = state_dict["expected_version"]
        actual = state_dict["actual_version"]
        status = state_dict["status"]
        sm_state = state_dict["state_machine_state"]
        
        # Safe-guard: Avoid downgriting a non-zero version to '0.0.0'
        if expected == "0.0.0" and actual != "0.0.0":
            state_dict["expected_version"] = actual
            expected = actual
            
        # Logging standard terminal states
        if status in ["success", "failure", "fail"] or expected == actual:
            log_status = status if status in ["success", "failure"] else "quiescent"
            if status == "fail":
                log_status = "failure"
            # Prevent double-logging same quiescent terminal state
            if sm_state != "quiescent" or log_status != "quiescent":
                # Ensure exactly formatted log output
                print(f"[butler] Device {site_id}/{device_id}/{blob_id} terminal state {log_status} with version {actual}")
                sys.stdout.flush()
                
        if expected == actual:
            state_dict["state_machine_state"] = "quiescent"
            state_dict["retry_count"] = 0
            state_dict["last_sent_time"] = 0.0
            return
            
        # If expected != actual, we have a version drift!
        if sm_state == "pending":
            # "system components MUST NOT interpret pre-update quiescent state reports (sent by the device before it receives the update command) as a termination of the pending transition"
            # If device explicitly reports pending, that's fine. If it reports success/failure with the target version, we transition out.
            if status == "pending":
                # Device is applying update
                return
            elif (status in ["success", "failure", "fail"]) and actual == expected:
                # Terminal transition completed
                state_dict["state_machine_state"] = "quiescent" if status == "success" else "failed"
                return
            else:
                # Still waiting for actual update report, ignore pre-update quiescent reports
                return
                
        # If sm_state is active, unknown, or failed, and expected != actual, trigger update
        state_dict["state_machine_state"] = "pending"
        state_dict["retry_count"] = 0
        state_dict["last_sent_time"] = time.time()
        
        self.trigger_update(site_id, device_id, blob_id)
        
    def trigger_update(self, site_id, device_id, blob_id):
        """Query Software Catalog, synthesize and publish config update."""
        key = (site_id, device_id, blob_id)
        state_dict = self.states[key]
        
        make = state_dict["make"]
        model = state_dict["model"]
        expected_version = state_dict["expected_version"]
        
        try:
            metadata = self.blobstore.resolve_package_metadata(make, model, blob_id, expected_version)
            if not metadata:
                sys.stderr.write(f"Could not resolve software catalog metadata for {make}/{model}/{blob_id}/{expected_version}\n")
                sys.stderr.flush()
                state_dict["state_machine_state"] = "failed"
                return
        except Exception as e:
            sys.stderr.write(f"BlobStore resolution error: {e}\n")
            sys.stderr.flush()
            state_dict["state_machine_state"] = "failed"
            return
            
        # Build config payload complying with UUFI standard
        config_payload = {
            "version": "1.5.2",
            "timestamp": self.get_utc_timestamp(),
            "blobset": {
                "blobs": {
                    blob_id: {
                        "phase": "apply",
                        "url": metadata["url"],
                        "sha256": metadata["sha256"],
                        "generation": self.get_utc_timestamp(),
                        "make": make,
                        "model": model,
                        "version": expected_version  # Any "blobset" update config command MUST include the target "version" attribute
                    }
                }
            }
        }
        
        # Build standard compliance Envelope (no subType, no deviceRegistryId)
        envelope = {
            "projectId": "vibrant",
            "transactionId": f"UUFI:butler-config:{uuid.uuid4().hex[:8]}",
            "publishTime": self.get_utc_timestamp(),
            "source": self.principal,
            "principal": f"impl_B.device",
            "nonce": secrets.token_hex(16),
            "payload": config_payload
        }
        
        # Publish topic
        topic = self.build_topic(f"uufi/r/{site_id}/d/{device_id}/c/config/blobset")
        self.client.publish(topic, json.dumps(envelope), qos=1)
        state_dict["last_command_payload"] = envelope
        sys.stderr.write(f"Triggered update command for {site_id}/{device_id}/{blob_id} -> version {expected_version} on {topic}\n")
        sys.stderr.flush()
        
    def timeout_loop(self):
        """Background thread checking for pending transition timeouts."""
        while self.running:
            time.sleep(1.0)
            now = time.time()
            with self.state_lock:
                for key, state_dict in list(self.states.items()):
                    site_id, device_id, blob_id = key
                    if state_dict["state_machine_state"] == "pending":
                        last_sent = state_dict["last_sent_time"]
                        if now - last_sent >= self.timeout:
                            # Timeout occurred!
                            state_dict["retry_count"] += 1
                            retries = state_dict["retry_count"]
                            if retries > 3:
                                # Terminal failure
                                sys.stderr.write(f"VERIFIER [ERROR]: VALIDATION ERROR: Device {site_id}/{device_id}/{blob_id} update transition timed out after 3 retries.\n")
                                sys.stderr.flush()
                                # Log standard terminal state failure
                                print(f"[butler] Device {site_id}/{device_id}/{blob_id} terminal state failure with version {state_dict['actual_version']}")
                                sys.stdout.flush()
                                state_dict["state_machine_state"] = "failed"
                                state_dict["status"] = "failure"
                            else:
                                # Retry publishing command
                                sys.stderr.write(f"Device {site_id}/{device_id}/{blob_id} pending timeout. Retrying update (attempt {retries}).\n")
                                sys.stderr.flush()
                                envelope = state_dict["last_command_payload"]
                                if envelope:
                                    # Refresh timestamps and nonce
                                    envelope["publishTime"] = self.get_utc_timestamp()
                                    envelope["nonce"] = secrets.token_hex(16)
                                    envelope["payload"]["timestamp"] = self.get_utc_timestamp()
                                    if "blobset" in envelope["payload"] and "blobs" in envelope["payload"]["blobset"]:
                                        for bid in envelope["payload"]["blobset"]["blobs"]:
                                            envelope["payload"]["blobset"]["blobs"][bid]["generation"] = self.get_utc_timestamp()
                                            
                                    topic = self.build_topic(f"uufi/r/{site_id}/d/{device_id}/c/config/blobset")
                                    self.client.publish(topic, json.dumps(envelope), qos=1)
                                    state_dict["last_sent_time"] = now
