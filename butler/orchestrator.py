import json
import time
import sys
import os
import argparse
import datetime
import threading
from butler.blob_repo import BlobRepository
from butler.model_repo import ModelRepository
from butler.messaging import create_payload, create_envelope
from butler.conn_spec import parse_conn_spec, get_default_registry_id, match_principal
from butler.transport import get_transport

class Orchestrator:
    def __init__(self, conn_spec, fail_mode=False):
        self.conn_spec = conn_spec
        self.blob_repo = BlobRepository()
        self.fail_mode = fail_mode
        self.transport = get_transport(conn_spec)
        self.lock = threading.RLock()
        self.pending_updates = {} # (registry_id, device_id, subsystem): {timestamp, target_version}
        self.is_active = False
        self.handshake_tid = None
        self.handshake_start_time = None
        self.activation_timeout = 60 # System SHOULD wait but NOT block indefinitely

        # ASSUMPTION: Sourcing is completely stateless across restarts (spec/butler.md Section 3.2 & 3).
        # Sourcing occurs exclusively reactively over the UUFI network interface, so in-memory tracking is reset.
        self.models = {}

        self.principal = self.conn_spec.principal or "butler"
        self.processed_transactions = {} # tid/nonce: timestamp

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
                    if now - self.processed_transactions[dedup_id] < 300: # 5 minutes
                        return
                self.processed_transactions[dedup_id] = now

            # Device state update / Handshake
            if sub_type == "state" and sub_folder == "udmi":
                # Check for Handshake Step 1 (setup block)
                setup = payload.get("setup")
                if setup:
                    if "udmi" in payload:
                        print(f"[butler] PROTOCOL VIOLATION: Handshake wrapped inside 'udmi' root. Rejecting.", flush=True)
                        return
                    # Filtering by principal for Handshake replies
                    target_principal = principal or source
                    self.handle_handshake_state(payload, tid, target_principal)
                    return
                
                # Device state updates (sourcing from system.software.<subsystem>)
                system_block = payload.get("system", {})
                software = system_block.get("software", {})
                if software:
                    for subsystem, sub_update in software.items():
                        if isinstance(sub_update, dict):
                            if not registry_id or not device_id: continue
                            
                            status = sub_update.get("status")
                            current_version = sub_update.get("version") or sub_update.get("current_version") or "0.0.0"
                            reported_lkg = sub_update.get("lkg_version") or "0.0.0"
                            make = sub_update.get("make")
                            model = sub_update.get("model")
                            
                            print(f"[butler] Status from {registry_id}/{device_id}/{subsystem}: {status} ({current_version}, reported lkg: {reported_lkg})", flush=True)
                            
                            key = (registry_id, device_id, subsystem)
                            dev_info = self.models.get(registry_id, {}).get(device_id, {}).get(subsystem, {})
                            
                            # LKG Management (Butler 2.2): Orchestrator is primary authority.
                            # Use cached LKG unless we're promoting a new one.
                            lkg_version = dev_info.get("lkg_version") or "0.0.0"

                            # Ingest make/model from state report if present (Butler 2.2)
                            if make or model:
                                # UUFI 8.5: Metadata Fallback - don't overwrite known with "unknown"
                                if make == "unknown" and dev_info.get("make") not in [None, "unknown"]:
                                    make = dev_info.get("make")
                                if model == "unknown" and dev_info.get("model") not in [None, "unknown"]:
                                    model = dev_info.get("model")

                                if (make and make != dev_info.get("make")) or (model and model != dev_info.get("model")):
                                    print(f"[butler] Ingesting metadata for {registry_id}/{device_id}/{subsystem}: {make}/{model}", flush=True)
                                    if registry_id not in self.models: self.models[registry_id] = {}
                                    if device_id not in self.models[registry_id]: self.models[registry_id][device_id] = {}
                                    if subsystem not in self.models[registry_id][device_id]: self.models[registry_id][device_id][subsystem] = {}
                                    if make: self.models[registry_id][device_id][subsystem]["make"] = make
                                    if model: self.models[registry_id][device_id][subsystem]["model"] = model

                            # Enforce state transition rule (Butler 4.3): Transitions to success/failure MUST occur from pending
                            in_pending = key in self.pending_updates
                            if status == "pending" and in_pending:
                                self.pending_updates[key]["has_reported_pending"] = True

                            # Reset BUTLER_TIMEOUT timer if specific, measurable blob progress update is present (Section 12.2)
                            has_progress = False
                            for k, v in sub_update.items():
                                kl = k.lower()
                                if any(x in kl for x in ["progress", "percent", "block"]):
                                    if v is not None:
                                        has_progress = True
                                        break
                            if in_pending and has_progress:
                                self.pending_updates[key]["timestamp"] = time.time()
                                print(f"[butler] Measurable progress detected for {key}, resetting timeout timer.", flush=True)

                            # Identify pre-update quiescent reports to avoid race conditions
                            is_pre_update_quiescent = False
                            if status == "quiescent" and in_pending:
                                target_version = self.pending_updates[key].get("target_version") or "0.0.0"
                                has_reported_pending = self.pending_updates[key].get("has_reported_pending", False)
                                has_reached_terminal = (current_version == target_version)
                                if not has_reported_pending and not has_reached_terminal:
                                    is_pre_update_quiescent = True

                            is_terminal = status in ["success", "failure", "quiescent"]
                            
                            if status in ["success", "failure"] and not in_pending and status != dev_info.get("status"):
                                print(f"[butler] PROTOCOL VIOLATION: {registry_id}/{device_id}/{subsystem} transitioned to {status} while not in PENDING state. Rejecting update.", flush=True)
                                continue

                            if is_terminal:
                                # ASSUMPTION: Pre-update quiescent state reports are not interpreted as termination of the pending transition
                                if not is_pre_update_quiescent:
                                    print(f"[butler] Device {registry_id}/{device_id}/{subsystem} terminal state {status} with version {current_version}", flush=True)
                                    if status == "success":
                                        # Butler 2.2: Successful update where current_version matches target_version MUST update lkg_version
                                        target_version = dev_info.get("target_version") or "0.0.0"
                                        if current_version == target_version and current_version != "0.0.0":
                                            if current_version != dev_info.get("lkg_version"):
                                                lkg_version = current_version
                            
                            # Version Safety (UUFI 8.4): Non-zero MUST NEVER be overwritten by "0.0.0"
                            if current_version == "0.0.0" and (dev_info.get("current_version") or "0.0.0") != "0.0.0":
                                current_version = dev_info.get("current_version")

                            # Robust model synchronization (Butler 2.2): update if version or status differs
                            should_sync = (current_version != dev_info.get("current_version") or 
                                           lkg_version != dev_info.get("lkg_version") or
                                           status != dev_info.get("status"))
                            
                            if should_sync:
                                print(f"[butler] Triggering model sync for {registry_id}/{device_id}/{subsystem}: {status} ({current_version}, lkg: {lkg_version})", flush=True)
                                self.update_cloud_model(registry_id, device_id, subsystem, current_version=current_version, lkg_version=lkg_version, status=status)
                            
                            if status in ["success", "failure", "quiescent"]:
                                if in_pending:
                                    # ASSUMPTION: If is_pre_update_quiescent is True, do not pop from self.pending_updates to keep the transition tracking active.
                                    if is_pre_update_quiescent:
                                        print(f"[butler] Pre-update quiescent report detected for {registry_id}/{device_id}/{subsystem}. Keeping pending update active.", flush=True)
                                    else:
                                        self.pending_updates.pop(key, None)
                                if status == "failure":
                                    self.rollback_cloud_model(registry_id, device_id, subsystem)
                
                if self.is_active:
                    self.check_reconciliation()
                return

            if sub_type == "config" and sub_folder == "udmi" and not device_id:
                if "udmi" in payload:
                    print(f"[butler] PROTOCOL VIOLATION: Handshake reply wrapped inside 'udmi'. Rejecting.", flush=True)
                    return
                if principal and not match_principal(principal, self.principal):
                    return
                self.handle_handshake_reply(payload, env.get("transactionId"))
                return

            # Cloud model query
            if sub_folder == "cloud" and sub_type == "query":
                cloud = payload.get("cloud", payload)
                if cloud.get("operation") == "READ":
                    self.push_full_model(principal=principal)
                return

            # Cloud model update
            if sub_folder == "cloud" and sub_type == "model":
                if "cloud" in payload:
                    print(f"[butler] PROTOCOL VIOLATION: Cloud model update wrapped inside 'cloud' root sub-object. Rejecting.", flush=True)
                    return
                registries = payload.get("registries", {})
                if not isinstance(registries, dict): return
                for reg_id, reg_data in registries.items():
                    if not isinstance(reg_data, dict): continue
                    if reg_id not in self.models:
                        self.models[reg_id] = {}
                    devices = reg_data.get("devices", {})
                    if not isinstance(devices, dict): continue
                    for dev_id, dev_data in devices.items():
                        if not isinstance(dev_data, dict): continue
                        if dev_id not in self.models[reg_id]:
                            self.models[reg_id][dev_id] = {}
                        
                        system_block = dev_data.get("system", {})
                        if not isinstance(system_block, dict): continue
                        
                        if "target_version" in system_block:
                            print(f"[butler] Ignoring prohibited target_version field at system configuration root.", flush=True)
                            
                        software = system_block.get("software", {})
                        if isinstance(software, dict):
                            for sub, ver in software.items():
                                if sub not in self.models[reg_id][dev_id]:
                                    self.models[reg_id][dev_id][sub] = {}
                                
                                if isinstance(ver, str):
                                    target_version = ver
                                elif isinstance(ver, dict):
                                    target_version = ver.get("version") or ver.get("current_version") or "0.0.0"
                                else:
                                    target_version = "0.0.0"
                                    
                                self.models[reg_id][dev_id][sub]["target_version"] = target_version
                                make = dev_data.get("make") or system_block.get("make")
                                model = dev_data.get("model") or system_block.get("model")
                                if make: self.models[reg_id][dev_id][sub]["make"] = make
                                if model: self.models[reg_id][dev_id][sub]["model"] = model
                                if "status" in system_block:
                                    self.models[reg_id][dev_id][sub]["status"] = system_block["status"]
                    print(f"[butler] Received model update for registry {reg_id}", flush=True)
                if self.is_active:
                    self.check_reconciliation()
                return

            # Discovery event
            if sub_type == "events" and sub_folder == "discovery":
                discovery = payload.get("discovery", payload)
                # Ingest make/model from discovery if present
                make = discovery.get("make")
                model = discovery.get("model")
                if registry_id and device_id and (make or model):
                    subsystem = discovery.get("subsystems", ["main"])[0]
                    print(f"[butler] Discovered {registry_id}/{device_id}/{subsystem}: {make}/{model}", flush=True)
                    self.update_cloud_model(registry_id, device_id, subsystem, make=make, model=model)
                if self.is_active:
                    self.check_reconciliation()
                return


    def handle_handshake_state(self, payload, tid, principal):
        udmi = payload.get("udmi", payload)
        setup = udmi.get("setup", {})
        msg_source = setup.get("msg_source", "unknown")
        
        print(f"[butler] Responding to handshake from {msg_source} ({principal})", flush=True)

        # Discovery (Butler 2.1): Populate initial model entry from Handshake Step 1
        if '/' in msg_source:
            registry_id, device_id = msg_source.split('/', 1)
            if registry_id and device_id:
                if registry_id not in self.models or device_id not in self.models[registry_id]:
                    print(f"[butler] Discovered device from handshake: {registry_id}/{device_id}", flush=True)
                    self.update_cloud_model(registry_id, device_id, "main", current_version="0.0.0", status="quiescent")

        if not self.is_active:
            print(f"[butler] Received initial handshake. Orchestrator is now ACTIVE.", flush=True)
            self.is_active = True
            self.query_all_registries()

        # UUFI Section 3: Orchestrator MAY provide registryId in handshake reply
        # Try to extract it from msg_source (registry_id/device_id)
        reply_registry_id = get_default_registry_id()
        if '/' in msg_source:
            reply_registry_id = msg_source.split('/')[0]

        reply_payload_data = {
            "setup": {
                "functions_min": 9,
                "functions_max": 9,
                "udmi_version": "1.5.2",
                "deviceRegistryId": reply_registry_id
            },
            "reply": {
                "functions_ver": 9,
                "msg_source": msg_source
            }
        }

        env = create_envelope(
            sub_type="config",
            sub_folder="udmi",
            transaction_id=tid,
            source=self.conn_spec.source_id,
            principal=principal
        )

        payload = create_payload("udmi", reply_payload_data, transaction_id=tid)
        self.transport.publish(env, payload)

    def handle_handshake_reply(self, payload, tid):
        # UUFI Section 3: udmi.reply.transaction_id -> reply.transaction_id
        udmi = payload.get("udmi", payload)
        reply = udmi.get("reply", {})
        reply_tid = reply.get("transaction_id")
        if reply_tid == self.handshake_tid:
            print(f"[butler] UUFI Handshake complete (tid: {reply_tid}). Orchestrator is ACTIVE.", flush=True)
            self.is_active = True
            # Proactively query for registries
            self.query_all_registries()

    def query_all_registries(self):
        # Query using the dedicated discovery topic /uufi/c/query/cloud
        env = create_envelope(
            sub_type="query",
            sub_folder="cloud",
            source=self.conn_spec.source_id,
            principal=self.principal
        )
        payload = create_payload("cloud", {"operation": "READ", "registries": {}})
        self.transport.publish(env, payload)

    def push_full_model(self, principal=None):
        # Push the entire model as a model/cloud message (Section 12.5)
        # Format: {"registries": {rid: {"devices": devs}}, "operation": "READ"}
        env = create_envelope(
            sub_type="model",
            sub_folder="cloud",
            source=self.conn_spec.source_id,
            principal=principal
        )
        payload_data = {
            "operation": "READ",
            "registries": {rid: {"devices": devs} for rid, devs in self.models.items()}
        }
        payload = create_payload("cloud", payload_data)
        self.transport.publish(env, payload)

    def update_cloud_model(self, registry_id, device_id, subsystem, target_version=None, current_version=None, lkg_version=None, status=None, make=None, model=None):
        dev_info = self.models.get(registry_id, {}).get(device_id, {}).get(subsystem, {})
        
        # UUFI 8.5: Metadata Fallback - don't overwrite known with "unknown"
        if make == "unknown" and dev_info.get("make") not in [None, "unknown"]:
            make = dev_info.get("make")
        if model == "unknown" and dev_info.get("model") not in [None, "unknown"]:
            model = dev_info.get("model")

        if make is None: make = dev_info.get("make") or "unknown"
        if model is None: model = dev_info.get("model") or "unknown"

        subsystem_data = {}
        # UUFI 8.4: Non-zero version MUST NEVER be overwritten by 0.0.0
        if target_version is not None:
            if not (target_version == "0.0.0" and (dev_info.get("target_version") or "0.0.0") != "0.0.0"):
                subsystem_data["target_version"] = target_version
        
        if current_version is not None:
            if not (current_version == "0.0.0" and (dev_info.get("current_version") or "0.0.0") != "0.0.0"):
                subsystem_data["current_version"] = current_version
        
        if lkg_version is not None:
            if not (lkg_version == "0.0.0" and (dev_info.get("lkg_version") or "0.0.0") != "0.0.0"):
                subsystem_data["lkg_version"] = lkg_version
        
        if status is not None: subsystem_data["status"] = status
        
        # Always include make/model in subsystem_data for cloud sync (UUFI 8.5)
        subsystem_data["make"] = make
        subsystem_data["model"] = model
        
        # Update internal cache
        if registry_id not in self.models: self.models[registry_id] = {}
        if device_id not in self.models[registry_id]: self.models[registry_id][device_id] = {}
        if subsystem not in self.models[registry_id][device_id]: self.models[registry_id][device_id][subsystem] = {}
        
        info = self.models[registry_id][device_id][subsystem]
        if target_version is not None: info["target_version"] = target_version
        if current_version is not None: info["current_version"] = current_version
        if lkg_version is not None: info["lkg_version"] = lkg_version
        if status is not None: info["status"] = status
        if make is not None: info["make"] = make
        if model is not None: info["model"] = model

        if not subsystem_data:
            return
        
        payload_data = {
            "operation": "UPDATE",
            "registries": {
                registry_id: {
                    "devices": {
                        device_id: {
                            subsystem: subsystem_data
                        }
                    }
                }
            }
        }

        # UUFI Section 3: Metadata SHOULD be stored in a dedicated 'meta' subsystem
        if make is not None or model is not None:
            metadata = {}
            if make: metadata["make"] = make
            if model: metadata["model"] = model
            payload_data["registries"][registry_id]["devices"][device_id]["meta"] = metadata
        
        env = create_envelope(
            registry_id=registry_id,
            device_id=device_id,
            sub_type="model",
            sub_folder="cloud",
            source=self.conn_spec.source_id
        )
        payload = create_payload("cloud", payload_data)
        self.transport.publish(env, payload)

    def rollback_cloud_model(self, registry_id, device_id, subsystem, is_timeout=False):
        # Compliance Note: The Orchestrator MUST NEVER trigger an update command to revert a device to an older LKG version upon failure or timeout.
        # Therefore, we simply report/record the terminal failure/timeout state without updating target_version back to lkg_version.
        devices = self.models.get(registry_id, {})
        dev_info = devices.get(device_id, {}).get(subsystem, {})
        status = dev_info.get("status")
        if is_timeout:
            if status in ["pending", "active"]:
                status = "failed"
            elif not status:
                status = "failed"
        else:
            if not status or status in ["pending", "active"]:
                status = "failure"
        self.update_cloud_model(registry_id, device_id, subsystem, status=status)

    def check_reconciliation(self):
        if not self.is_active:
            return
        
        now = time.time()
        for registry_id, devices in self.models.items():
            for device_id, subsystems in devices.items():
                if not isinstance(subsystems, dict): continue
                for subsystem, info in subsystems.items():
                    if not isinstance(info, dict): continue
                    
                    key = (registry_id, device_id, subsystem)
                    
                    target = info.get("target_version") or "0.0.0"
                    current = info.get("current_version") or "0.0.0"
                    status = info.get("status")
                    
                    retrigger = False
                    if key in self.pending_updates:
                        pending_target = self.pending_updates[key]["target_version"]
                        if target != pending_target and target != current:
                            print(f"[butler] Retriggering {key}: target {target} != pending {pending_target}", flush=True)
                            retrigger = True
                    elif (target != "0.0.0" and target != current) or status == "active":
                        retrigger = True

                    if retrigger:
                        print(f"[butler] Reconciliation triggered for {registry_id}/{device_id}: {current} -> {target}", flush=True)
                        
                        if self.fail_mode: continue

                        make = info.get("make") or "unknown"
                        model = info.get("model") or "unknown"

                        metadata = self.blob_repo.get_blob_metadata(
                            make, model, subsystem, target
                        )
                        
                        if metadata:
                            update_data = {
                                "blobs": {
                                    subsystem: {
                                        "phase": "apply",
                                        "version": target,
                                        "generation": datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                                        "url": metadata["url"],
                                        "sha256": metadata["sha256"],
                                        "make": make,
                                        "model": model
                                    }
                                }
                            }
                            env = create_envelope(
                                registry_id=registry_id,
                                device_id=device_id,
                                sub_type="config",
                                sub_folder="blobset",
                                source=self.conn_spec.source_id
                            )
                            payload = create_payload("blobset", update_data)
                            self.transport.publish(env, payload)
                            self.pending_updates[key] = {
                                "timestamp": time.time(),
                                "target_version": target
                            }

    def check_timeouts(self):
        now = time.time()
        
        # Deduplication cleanup (Butler 8)
        to_clear = [dedup_id for dedup_id, ts in self.processed_transactions.items() if now - ts > 300]
        for dedup_id in to_clear:
            del self.processed_transactions[dedup_id]

        timeout = int(os.environ.get("BUTLER_TIMEOUT", 60))
        to_remove = []
        for key, info in self.pending_updates.items():
            if now - info["timestamp"] > timeout:
                registry_id, device_id, subsystem = key
                print(f"[butler] Timeout for {registry_id}/{device_id}/{subsystem}. Rolling back...", flush=True)
                self.rollback_cloud_model(registry_id, device_id, subsystem, is_timeout=True)
                to_remove.append(key)
        
        for k in to_remove:
            self.pending_updates.pop(k, None)

    def run(self):
        self.transport.connect()
        
        # Subscribe to handshake reply and discovery
        if self.conn_spec.protocol == "mqtt":
            # New unified topics: /uufi/c/...
            prefix = self.conn_spec.prefix + '/' if self.conn_spec.prefix else ''
            self.transport.subscribe(f"/{prefix}uufi/c/state/udmi", self.on_message)
            self.transport.subscribe(f"/{prefix}uufi/c/config/udmi", self.on_message)
            self.transport.subscribe(f"/{prefix}uufi/c/query/cloud", self.on_message)
            self.transport.subscribe(f"/{prefix}uufi/c/model/cloud", self.on_message)
            self.transport.subscribe(f"/{prefix}uufi/c/events/discovery", self.on_message)
            self.transport.subscribe(f"/{prefix}uufi/r/+/d/+/c/state/udmi", self.on_message)
            self.transport.subscribe(f"/{prefix}uufi/r/+/d/+/c/model/cloud", self.on_message)
        else:
            self.transport.subscribe(self.on_message)

        self.transport.loop_start()
        
        self.handshake_start_time = time.time()
        try:
            while True:
                now = time.time()
                with self.lock:
                    if not self.is_active:
                        if not self.transport.is_connected:
                            # Reset handshake start time until we actually connect
                            self.handshake_start_time = now
                    else:
                        self.check_reconciliation()
                        self.check_timeouts()
                time.sleep(2)
        except KeyboardInterrupt:
            self.transport.loop_stop()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("pos_conn_spec", nargs="?", help="Connection spec URL")
    parser.add_argument("--conn_spec", help="Connection spec URL")
    parser.add_argument("-f", action="store_true", help="Enable failure mode")
    args, unknown = parser.parse_known_args()

    conn_str = args.conn_spec or args.pos_conn_spec
    conn_spec = parse_conn_spec(conn_str, differentiator="butler")
    sys.stderr.write(f"{conn_spec.format_conn_spec()}\n")
    orchestrator = Orchestrator(conn_spec, fail_mode=args.f)
    orchestrator.run()

if __name__ == "__main__":
    main()
