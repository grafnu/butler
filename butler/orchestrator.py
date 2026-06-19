import os
import sys
import json
import time
import threading
from butler.uufi import UUFIClient, get_timestamp
from butler.blobstore import get_blobstore_provider

class ButlerOrchestrator:
    def __init__(self, conn_spec):
        self.client = UUFIClient(conn_spec, "butler")
        self.blobstore = get_blobstore_provider()
        self.timeout = int(os.environ.get("BUTLER_TIMEOUT", "60"))
        
        # In-memory volatile state
        # Structure:
        # self.expected_versions[site_id][device_id][blob_id] = version_tag
        self.expected_versions = {}
        
        # self.devices[site_id][device_id][blob_id] = {
        #     "actual_version": str,
        #     "lkg_version": str,
        #     "status": str, # success, failure, quiescent, pending
        #     "tracking_state": str, # unknown, quiescent, active, pending, failed
        #     "make": str,
        #     "model": str,
        #     "retry_count": int,
        #     "last_command_time": float,
        #     "last_command_payload": dict
        # }
        self.devices = {}
        self.lock = threading.Lock()
        
        self.running = False
        self.timeout_thread = None

    def start(self):
        self.running = True
        
        if not self.client.connect():
            print("Failed to start Butler Orchestrator - MQTT connection failed", file=sys.stderr)
            return False

        # 1. Setup handshake responder
        self.client.setup_handshake_responder(self._on_client_active)

        # 2. Subscribe to Model Updates and Device States
        # Model updates: [/{prefix}]/uufi/c/model/cloud
        # Model replies: [/{prefix}]/uufi/c/config/cloud
        # Device states: [/{prefix}]/uufi/r/+/d/+/state/blobset
        self.client.subscribe(self.client.build_topic("model", "cloud"), self._on_model_update)
        self.client.subscribe(self.client.build_topic("config", "cloud"), self._on_model_reply)
        self.client.subscribe(self.client.build_topic("state", "blobset", "+", "+"), self._on_device_state)

        # 3. Start background timeout checking thread
        self.timeout_thread = threading.Thread(target=self._timeout_loop, daemon=True)
        self.timeout_thread.start()

        # 4. Publish startup UUFI Model Query
        query_topic = self.client.build_topic("query", "cloud")
        print(f"Publishing startup Model Query to {query_topic}...", file=sys.stderr)
        self.client.publish(query_topic, {"version": "1.5.2", "timestamp": get_timestamp()})

        print("Butler Orchestrator started and running.", file=sys.stderr)
        return True

    def stop(self):
        self.running = False
        self.client.disconnect()
        if self.timeout_thread:
            self.timeout_thread.join(timeout=2)

    def _on_client_active(self, principal):
        print(f"Client became active: {principal}", file=sys.stderr)

    def _on_model_reply(self, topic, envelope):
        self._on_model_update(topic, envelope)

    def _on_model_update(self, topic, envelope):
        # Sourced over UUFI bus
        # Extract registries -> {site_id} -> devices -> {device_id} -> system -> software -> {blob_id} = version_tag
        payload = envelope.get("payload", {})
        registries = payload.get("registries", {})
        
        if not registries:
            return

        with self.lock:
            for site_id, site_data in registries.items():
                devices_data = site_data.get("devices", {})
                for device_id, device_data in devices_data.items():
                    system_data = device_data.get("system", {})
                    software_data = system_data.get("software", {})
                    
                    for blob_id, expected_version in software_data.items():
                        if site_id not in self.expected_versions:
                            self.expected_versions[site_id] = {}
                        if device_id not in self.expected_versions[site_id]:
                            self.expected_versions[site_id][device_id] = {}
                            
                        # Partial merge check: only update if expected_version is provided and not null
                        if expected_version is not None:
                            old_exp = self.expected_versions[site_id][device_id].get(blob_id)
                            if old_exp != expected_version:
                                self.expected_versions[site_id][device_id][blob_id] = expected_version
                                print(f"[butler] Updated expected version for {site_id}/{device_id}/{blob_id}: {expected_version}", file=sys.stderr)
                                self._reevaluate(site_id, device_id, blob_id)

    def _on_device_state(self, topic, envelope):
        topic_info = self.client.parse_topic(topic)
        if not topic_info:
            return
            
        site_id = topic_info["site_id"]
        device_id = topic_info["device_id"]
        
        payload = envelope.get("payload", {})
        blobset = payload.get("blobset", {})
        blobs = blobset.get("blobs", {})
        
        # Device state reports are authoritative and must be processed immediately
        with self.lock:
            for blob_id, blob_state in blobs.items():
                # Extract actual version
                actual_version = blob_state.get("current_version") or blob_state.get("version")
                # Default to "0.0.0" if unknown as per spec
                if not actual_version:
                    actual_version = "0.0.0"
                    
                status = blob_state.get("status") or blob_state.get("phase") or "unknown"
                lkg_version = blob_state.get("lkg_version") or "0.0.0"
                make = blob_state.get("make") or "unknown"
                model = blob_state.get("model") or "unknown"

                if site_id not in self.devices:
                    self.devices[site_id] = {}
                if device_id not in self.devices[site_id]:
                    self.devices[site_id][device_id] = {}
                    
                dev_info = self.devices[site_id][device_id].get(blob_id)
                if not dev_info:
                    dev_info = {
                        "actual_version": "0.0.0",
                        "lkg_version": "0.0.0",
                        "status": "unknown",
                        "tracking_state": "unknown",
                        "make": "unknown",
                        "model": "unknown",
                        "retry_count": 0,
                        "last_command_time": 0.0,
                        "last_command_payload": None
                    }
                    self.devices[site_id][device_id][blob_id] = dev_info

                # Update reported values
                # Type safety: a non-zero version string MUST NEVER be overwritten by "0.0.0"
                if actual_version != "0.0.0" or dev_info["actual_version"] == "0.0.0":
                    dev_info["actual_version"] = actual_version
                    
                if lkg_version != "0.0.0" or dev_info["lkg_version"] == "0.0.0":
                    dev_info["lkg_version"] = lkg_version
                    
                if make != "unknown" or dev_info["make"] == "unknown":
                    dev_info["make"] = make
                    
                if model != "unknown" or dev_info["model"] == "unknown":
                    dev_info["model"] = model
                    
                dev_info["status"] = status

                print(f"[butler] Received State Report for {site_id}/{device_id}/{blob_id}: actual={actual_version}, status={status}", file=sys.stderr)
                self._reevaluate(site_id, device_id, blob_id)

    def _reevaluate(self, site_id, device_id, blob_id):
        # Re-evaluate state for this device blob
        expected = self.expected_versions.get(site_id, {}).get(device_id, {}).get(blob_id)
        dev_info = self.devices.get(site_id, {}).get(device_id, {}).get(blob_id)
        
        if not expected or not dev_info:
            return

        actual = dev_info["actual_version"]
        status = dev_info["status"]
        tracking = dev_info["tracking_state"]

        # 1. Check if expected matches actual (quiescent)
        if expected == actual:
            if tracking != "quiescent":
                dev_info["tracking_state"] = "quiescent"
                dev_info["retry_count"] = 0
                # Log terminal state
                print(f"[butler] Device {site_id}/{device_id}/{blob_id} terminal state quiescent with version {actual}")
                sys.stdout.flush()
            return

        # 2. Expected != Actual (Version drift)
        # If the device is currently in a "pending" status, we should track it as pending
        if status == "pending":
            if tracking != "pending":
                dev_info["tracking_state"] = "pending"
                print(f"[butler] Device {site_id}/{device_id}/{blob_id} transitioned to pending update.", file=sys.stderr)
            return

        # If we are already in "pending" tracking state, but status is terminal (success/failure)
        # and version drift still exists:
        if tracking == "pending" and (status == "success" or status == "failure"):
            dev_info["tracking_state"] = "failed"
            dev_info["retry_count"] = 0
            # Log terminal state
            print(f"[butler] Device {site_id}/{device_id}/{blob_id} terminal state {status} with version {actual}")
            sys.stdout.flush()
            return

        # Trigger update if we are not in pending tracking state
        if dev_info["tracking_state"] not in ["pending", "failed"]:
            self._trigger_update(site_id, device_id, blob_id, expected, dev_info)

    def _trigger_update(self, site_id, device_id, blob_id, expected_version, dev_info):
        make = dev_info["make"]
        model = dev_info["model"]
        
        print(f"[butler] Resolving metadata for {make}/{model}/{blob_id}/{expected_version}...", file=sys.stderr)
        try:
            metadata = self.blobstore.resolve_package_metadata(make, model, blob_id, expected_version)
        except Exception as e:
            print(f"[butler] ERROR: Failed to resolve metadata for {make}/{model}/{blob_id}/{expected_version}: {e}", file=sys.stderr)
            return

        # Build blobset config update command
        config_payload = {
            "version": "1.5.2",
            "timestamp": get_timestamp(),
            "blobset": {
                "blobs": {
                    blob_id: {
                        "phase": "apply",
                        "url": metadata.url,
                        "sha256": metadata.sha256,
                        "generation": get_timestamp()
                    }
                }
            }
        }

        config_topic = self.client.build_topic("config", "blobset", site_id, device_id)
        
        print(f"[butler] Publishing update command to {config_topic}...", file=sys.stderr)
        self.client.publish(config_topic, config_payload)
        
        dev_info["tracking_state"] = "pending"
        dev_info["retry_count"] = 0
        dev_info["last_command_time"] = time.time()
        dev_info["last_command_payload"] = config_payload
        
        # Logging terminal state trigger
        print(f"[butler] Device {site_id}/{device_id}/{blob_id} terminal state active with version {dev_info['actual_version']}")
        sys.stdout.flush()

    def _timeout_loop(self):
        while self.running:
            time.sleep(1)
            with self.lock:
                now = time.time()
                for site_id, site_devs in self.devices.items():
                    for device_id, dev_blobs in site_devs.items():
                        for blob_id, dev_info in dev_blobs.items():
                            if dev_info["tracking_state"] == "pending":
                                elapsed = now - dev_info["last_command_time"]
                                if elapsed > self.timeout:
                                    retry_count = dev_info["retry_count"]
                                    if retry_count < 3:
                                        # Retry command
                                        print(f"[butler] WARNING: Timeout pending transition for {site_id}/{device_id}/{blob_id}. Retrying {retry_count + 1}/3...", file=sys.stderr)
                                        config_topic = self.client.build_topic("config", "blobset", site_id, device_id)
                                        self.client.publish(config_topic, dev_info["last_command_payload"])
                                        
                                        dev_info["retry_count"] += 1
                                        dev_info["last_command_time"] = now
                                    else:
                                        # Max retries exhausted
                                        print(f"[butler] WARNING: Terminal failure for {site_id}/{device_id}/{blob_id} - all retry attempts exhausted.", file=sys.stderr)
                                        dev_info["tracking_state"] = "failed"
                                        # Log terminal state
                                        print(f"[butler] Device {site_id}/{device_id}/{blob_id} terminal state failure with version {dev_info['actual_version']}")
                                        sys.stdout.flush()
