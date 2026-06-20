import json
import time
import sys
import os
import hashlib
import argparse
import datetime
import threading
from butler.messaging import create_payload, create_envelope
from butler.model_repo import ModelRepository
from butler.conn_spec import parse_conn_spec, split_device_id, match_principal
from butler.transport import get_transport

class MockDevice:
    def __init__(self, conn_spec, registry_id, device_id, fail_mode=False):
        self.conn_spec = conn_spec
        self.registry_id = registry_id
        self.device_id = device_id
        self.fail_mode = fail_mode
        self.lock = threading.RLock()
        self.current_version = "0.0.0" 
        self.lkg_version = "0.0.0"
        self.target_version = "0.0.0"
        self.state = "quiescent"
        self.transport = get_transport(conn_spec)
        self.subsystem = "system"
        self.model_repo = ModelRepository()
        
        # Initialize state from model repo if available
        dev_info = self.model_repo.get_device_state(self.registry_id, self.device_id, self.subsystem)
        if dev_info:
            self.current_version = dev_info.get("current_version", "0.0.0")
            self.lkg_version = dev_info.get("lkg_version", "0.0.0")
            self.target_version = dev_info.get("target_version", "0.0.0")
            self.make = dev_info.get("make", "unknown")
            self.model = dev_info.get("model", "unknown")
            print(f"[mocket] Initialized {self.device_id} current_version to {self.current_version}, lkg to {self.lkg_version}, target to {self.target_version}", flush=True)
        else:
            self.current_version = "0.0.0"
            self.lkg_version = "0.0.0"
            self.target_version = "0.0.0"
            self.make = "unknown"
            self.model = "unknown"

        self.my_id = f"{self.registry_id}/{self.device_id}"
        self.is_active = False
        self.handshake_tid = None
        self.handshake_start_time = None
        self.processed_transactions = {} # tid/nonce: timestamp

    def send_handshake(self):
        import secrets
        self.handshake_tid = secrets.token_hex(4)
        if self.handshake_start_time is None:
            self.handshake_start_time = time.time()
        udmi_payload = {
            "setup": {
                "functions_ver": 9,
                "msg_source": self.my_id,
                "user": self.conn_spec.username
            }
        }
        env = create_envelope(
            sub_type="state",
            sub_folder="udmi",
            transaction_id=self.handshake_tid,
            source=self.conn_spec.source_id,
            principal=self.conn_spec.principal
        )
            
        payload = create_payload("udmi", udmi_payload, transaction_id=self.handshake_tid)
        self.transport.publish(env, payload)

    def handle_handshake_reply(self, payload, tid):
        if "udmi" in payload:
            print(f"[mocket] PROTOCOL VIOLATION: Handshake wrapped inside 'udmi'. Rejecting.", flush=True)
            return
        if tid != self.handshake_tid:
            print(f"[mocket] Handshake reply transaction ID mismatch: expected {self.handshake_tid}, got {tid}. Rejecting.", flush=True)
            return
        reply = payload.get("reply", {})
        reply_tid = reply.get("transaction_id")
        if reply_tid == self.handshake_tid:
            # UUFI Section 3: Priority: Pre-configured registry ID MUST be prioritized over 
            # the one provided by the System during handshake.
            # Since mocket requires registry_id on CLI, we ALWAYS have a pre-configured one.
            pass

            print(f"[mocket] UUFI Handshake complete (tid: {reply_tid}). Device is ACTIVE.", flush=True)
            self.is_active = True
            self.report_status()

    def subscribe_all(self):
        if self.conn_spec.protocol == "mqtt":
            prefix = self.conn_spec.prefix + '/' if self.conn_spec.prefix else ''
            # Generic UUFI channel for handshakes and discovery
            self.transport.subscribe(f"/{prefix}uufi/c/+/+", self.on_message)
            # Registry-level channel (UUFI Section 2.2)
            self.transport.subscribe(f"/{prefix}uufi/r/{self.registry_id}/c/+/+", self.on_message)
            # Device-specific channel (UUFI Section 2.2)
            self.transport.subscribe(f"/{prefix}uufi/r/{self.registry_id}/d/{self.device_id}/c/+/+", self.on_message)

    def on_message(self, env, payload, topic, raw=None):
        if not payload: return
        
        with self.lock:
            source = env.get("source")
            tid = env.get("transactionId")
            nonce = env.get("nonce")
            sub_folder = env.get("subFolder")
            sub_type = env.get("subType")
            device_id = env.get("deviceId")
            registry_id = env.get("deviceRegistryId")
            principal = env.get("principal")

            # Deduplication (UUFI Section 7.3 & 9): Use nonce if present, else transactionId
            # Handshake messages (udmi subfolder) should bypass deduplication.
            dedup_id = nonce or tid
            if dedup_id and sub_folder != "udmi":
                now = time.time()
                if dedup_id in self.processed_transactions:
                    if now - self.processed_transactions[dedup_id] < 300:
                        return
                self.processed_transactions[dedup_id] = now

            # UUFI handshake config from systems
            if sub_type == "config" and sub_folder == "udmi" and not device_id:
                # UUFI Section 2.2: principal MUST match Client's identity
                if principal and not match_principal(principal, self.conn_spec.principal):
                    return
                self.handle_handshake_reply(payload, env.get("transactionId"))
                return

            # Cloud ops
            if sub_folder == "cloud" and (
                (registry_id == self.registry_id and (device_id == self.registry_id or device_id == self.device_id)) or
                (not registry_id and not device_id) # Discovery query
            ):
                self.handle_cloud_op(sub_type, payload)
                return

            # Update config
            if registry_id == self.registry_id and device_id == self.device_id and sub_type == "config" and sub_folder == "blobset":
                blobset = payload.get("blobset", {})
                
                # UUFI Section 8.1 / UDMI Standard: subsystems are inside 'blobs' wrapper
                if "blobs" in blobset and isinstance(blobset["blobs"], dict):
                    config = blobset["blobs"].get(self.subsystem)
                elif self.subsystem in blobset and isinstance(blobset[self.subsystem], dict):
                    # Fallback for unnested (flat) payloads with subsystem as key
                    config = blobset[self.subsystem]
                elif not any(isinstance(v, dict) for v in blobset.values()):
                    # Very flat payload
                    config = blobset
                else:
                    config = None

                if not config or not isinstance(config, dict):
                    return

                url = config.get("url")
                sha256 = config.get("sha256")
                # UUFI 8.1: Metadata (make, model) are mandatory for all blobset payloads
                # UUFI 8.5: Metadata Fallback - don't overwrite known with "unknown"
                new_make = config.get("make")
                if new_make and not (new_make == "unknown" and self.make != "unknown"):
                    self.make = new_make
                
                new_model = config.get("model")
                if new_model and not (new_model == "unknown" and self.model != "unknown"):
                    self.model = new_model
                
                # UUFI 8.1: generation MUST be included in blobset config payloads
                version = config.get("version") or config.get("generation")

                if not version or not url: 
                    print(f"[mocket] Missing version (or generation) or url in blobset config", flush=True)
                    return

                print(f"[mocket] Device {self.device_id} received update to {version}", flush=True)
                self.target_version = version
                self.state = "pending"
                self.report_status()

                if self.fail_mode:
                    print(f"[mocket] FAILURE MODE: Not progressing from pending.", flush=True)
                    return

                time.sleep(1)
                try:
                    local_path = url
                    if url.startswith("file://"):
                        # UUFI Section 8.1: Strip the scheme and any leading slashes as appropriate for the OS.
                        # file:///path -> /path (absolute)
                        # file://path  -> path (relative)
                        local_path = url[7:]
                        if local_path.startswith('//'):
                            # file:////path -> //path (unc-like, but on Linux might be just /path)
                            local_path = local_path[1:]
                    
                    if not os.path.exists(local_path):
                        print(f"[mocket] Blob not found: {local_path}", flush=True)
                        self.state = "failure"
                        self.report_status(category="blob_invalid")
                        return

                    with open(local_path, "rb") as f:
                        import hashlib
                        actual_hash = hashlib.sha256(f.read()).hexdigest()

                    if actual_hash == sha256:
                        time.sleep(1)
                        self.current_version = version
                        self.lkg_version = version
                        self.state = "success"
                        self.report_status()
                        self.state = "quiescent"
                        self.report_status()
                    else:
                        print(f"[mocket] Hash mismatch for {url}: expected {sha256}, got {actual_hash}", flush=True)
                        self.state = "failure"
                        self.report_status(category="blob_invalid")
                except Exception as e:
                    print(f"[mocket] Error applying update: {e}", flush=True)
                    self.state = "failure"
                    self.report_status(category="apply_error")


    def handle_cloud_op(self, sub_type, payload):
        # Butler Section 2.1: Butler is the sole authoritative Cloud Model Server.
        # Mocket MUST NOT respond to query/cloud or unilaterally publish config/cloud.
        pass

    def push_model(self):
        # Mocket should not proactively push model updates to the bus.
        pass

    def report_status(self, category=None):
        sub_data = {
            "current_version": self.current_version or "0.0.0",
            "version": self.current_version or "0.0.0",
            "target_version": self.target_version or "0.0.0",
            "lkg_version": self.lkg_version or "0.0.0",
            "status": self.state,
            "make": self.make,
            "model": self.model
        }
        if category:
            sub_data["category"] = category
        if self.state == "pending":
            # Simulate measurable active progress update to test timer resets (Section 12.2)
            sub_data["progress"] = 50.0
            sub_data["download_percentage"] = 50.0
            
        update_data = {
            "system": {
                "software": {
                    self.subsystem: sub_data
                }
            }
        }
        env = create_envelope(
            registry_id=self.registry_id,
            device_id=self.device_id,
            sub_type="state",
            sub_folder="udmi",
            source=self.conn_spec.source_id
        )
        payload = create_payload("udmi", update_data)
        self.transport.publish(env, payload)

    def send_discovery(self):
        discovery_payload = {
            "version": "1.5.2",
            "timestamp": datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            "make": self.make,
            "model": self.model,
            "subsystems": [self.subsystem]
        }
        env = create_envelope(
            registry_id=self.registry_id,
            device_id=self.device_id,
            sub_type="events",
            sub_folder="discovery",
            source=self.conn_spec.source_id,
            principal=self.conn_spec.principal
        )
        payload = create_payload("discovery", discovery_payload)
        self.transport.publish(env, payload)

    def run(self):
        self.transport.connect()
        self.subscribe_all()
        self.transport.loop_start()
        
        self.handshake_start_time = time.time()
        last_handshake = 0
        last_push = 0
        last_status = 0
        try:
            while True:
                now = time.time()
                with self.lock:
                    # Deduplication cleanup
                    to_clear = [dedup_id for dedup_id, ts in self.processed_transactions.items() if now - ts > 300]
                    for dedup_id in to_clear:
                        del self.processed_transactions[dedup_id]

                    if not self.is_active:
                        if self.transport.is_connected:
                            if now - self.handshake_start_time > 60:
                                print("[mocket] CRITICAL: Handshake timeout. Fail-fast.", flush=True)
                                sys.exit(1)
                            if now - last_handshake > 5:
                                self.send_handshake()
                                last_handshake = now
                        else:
                            self.handshake_start_time = now
                    else:
                        if last_push == 0:
                            # Send initial discovery upon activation
                            self.send_discovery()
                            last_push = now
                        
                        if now - last_status > 10:
                            self.report_status()
                            last_status = now
                
                time.sleep(1)
        except KeyboardInterrupt:
            self.transport.loop_stop()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("pos_args", nargs="*", help="[conn_spec] <registry_id> <device_id>")
    parser.add_argument("--conn_spec", help="Connection spec URL")
    parser.add_argument("-f", action="store_true", help="Enable failure mode")
    args, unknown = parser.parse_known_args()

    pos_args = args.pos_args
    conn_str = args.conn_spec
    if not conn_str and pos_args and ("://" in pos_args[0]):
        conn_str = pos_args.pop(0)

    if len(pos_args) < 2:
        print("Error: registry_id and device_id are required", file=sys.stderr)
        sys.exit(1)

    registry_id = pos_args[0]
    device_id = pos_args[1]

    conn_spec = parse_conn_spec(conn_str, differentiator="device")
    sys.stderr.write(f"{conn_spec.format_conn_spec()}\n")
    device = MockDevice(conn_spec, registry_id, device_id, fail_mode=args.f)
    device.run()

if __name__ == "__main__":
    main()
