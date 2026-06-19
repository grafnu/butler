import os
import json
import datetime

class ModelRepository:
    def __init__(self, file_path=None):
        if file_path is None:
            file_path = os.environ.get("BUTLER_MODEL_FILE", "testing/model.json")
        self.file_path = file_path
        self._load()

    def _load(self):
        import fcntl
        if os.path.exists(self.file_path):
            with open(self.file_path, 'r') as f:
                try:
                    fcntl.flock(f, fcntl.LOCK_SH)
                    raw_data = json.load(f)
                    fcntl.flock(f, fcntl.LOCK_UN)
                except (json.JSONDecodeError, Exception):
                    raw_data = {"registries": {}}
        else:
            raw_data = {"registries": {}}
        
        # Migration and compliance handling
        if "cloud" in raw_data and "registries" in raw_data["cloud"]:
            # Already compliant with Section 10.4
            self.data = raw_data
        elif "registries" in raw_data:
            # Semi-compliant (legacy)
            self.data = {
                "version": "1.5.2",
                "timestamp": datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                "cloud": {
                    "operation": "READ",
                    "registries": raw_data["registries"]
                }
            }
        elif "devices" in raw_data:
            # Very old format
            self.data = {
                "version": "1.5.2",
                "timestamp": datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                "cloud": {
                    "operation": "READ",
                    "registries": {
                        "default": {"devices": raw_data["devices"]}
                    }
                }
            }
        else:
            self.data = {
                "version": "1.5.2",
                "timestamp": datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                "cloud": {
                    "operation": "READ",
                    "registries": {}
                }
            }

    def reload(self):
        self._load()

    def _save(self):
        import fcntl
        dir_path = os.path.dirname(self.file_path)
        if dir_path and not os.path.exists(dir_path):
            os.makedirs(dir_path)
        
        # Update timestamp before saving for compliance (UUFI Section 8.2)
        self.data["timestamp"] = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        
        # Use a lock file to coordinate between processes
        lock_file_path = self.file_path + ".lock"
        with open(lock_file_path, "w") as lf:
            try:
                fcntl.flock(lf, fcntl.LOCK_EX)
                
                import tempfile
                fd, temp_path = tempfile.mkstemp(dir=dir_path or ".", suffix=".tmp")
                try:
                    with os.fdopen(fd, 'w') as f:
                        json.dump(self.data, f, indent=4)
                    os.replace(temp_path, self.file_path)
                except Exception:
                    if os.path.exists(temp_path):
                        try:
                            os.remove(temp_path)
                        except Exception:
                            pass
                    raise
                finally:
                    fcntl.flock(lf, fcntl.LOCK_UN)
            except Exception as e:
                print(f"Error during locked save: {e}", file=sys.stderr)

    def _ensure_registry_device(self, registry_id, device_id, subsystem):
        registries = self.data["cloud"]["registries"]
        if registry_id not in registries:
            registries[registry_id] = {"devices": {}}
        if "devices" not in registries[registry_id]:
            registries[registry_id]["devices"] = {}
        if device_id not in registries[registry_id]["devices"]:
            registries[registry_id]["devices"][device_id] = {}
        if subsystem not in registries[registry_id]["devices"][device_id]:
            registries[registry_id]["devices"][device_id][subsystem] = {
                "current_version": "0.0.0",
                "target_version": "0.0.0",
                "lkg_version": "0.0.0",
                "status": "quiescent",
                "make": "unknown",
                "model": "unknown"
            }

    def set_device_info(self, registry_id, device_id, subsystem, make=None, model=None):
        self._ensure_registry_device(registry_id, device_id, subsystem)
        dev_data = self.data["cloud"]["registries"][registry_id]["devices"][device_id]
        sub_info = dev_data[subsystem]
        
        info_update = {}
        # UUFI 8.5: A known non-fallback value MUST NEVER be overwritten by "unknown"
        if make is not None and make != "unknown":
            info_update["make"] = make
        elif make == "unknown" and sub_info.get("make") == "unknown":
            info_update["make"] = "unknown"

        if model is not None and model != "unknown":
            info_update["model"] = model
        elif model == "unknown" and sub_info.get("model") == "unknown":
            info_update["model"] = "unknown"
        
        if info_update:
            sub_info.update(info_update)
            # Also ensure 'meta' subsystem has it for compatibility (Butler 2.2)
            if "meta" not in dev_data:
                dev_data["meta"] = {}
            dev_data["meta"].update(info_update)
            self._save()

    def set_target_version(self, registry_id, device_id, subsystem, version):
        self._ensure_registry_device(registry_id, device_id, subsystem)
        dev = self.data["cloud"]["registries"][registry_id]["devices"][device_id][subsystem]
        
        # UUFI 8.4: Non-zero version MUST NEVER be overwritten by 0.0.0
        if version == "0.0.0" and (dev.get("target_version") or "0.0.0") != "0.0.0":
            return

        dev["target_version"] = version
        self._save()

    def update_current_version(self, registry_id, device_id, subsystem, version, lkg_version=None, status=None):
        self._ensure_registry_device(registry_id, device_id, subsystem)
        dev = self.data["cloud"]["registries"][registry_id]["devices"][device_id][subsystem]
        
        if version is not None:
            # UUFI 8.4: Non-zero version MUST NEVER be overwritten by 0.0.0
            if not (version == "0.0.0" and (dev.get("current_version") or "0.0.0") != "0.0.0"):
                dev["current_version"] = version
        
        if lkg_version is not None:
            # UUFI 8.4: Non-zero version MUST NEVER be overwritten by 0.0.0
            if not (lkg_version == "0.0.0" and (dev.get("lkg_version") or "0.0.0") != "0.0.0"):
                dev["lkg_version"] = lkg_version
        
        if status is not None:
            dev["status"] = status
        self._save()

    def get_device_state(self, registry_id, device_id, subsystem):
        return self.data["cloud"].get("registries", {}).get(registry_id, {}).get("devices", {}).get(device_id, {}).get(subsystem)

    def get_all_registries(self):
        return self.data["cloud"].get("registries", {})

    def rollback(self, registry_id, device_id, subsystem):
        state = self.get_device_state(registry_id, device_id, subsystem)
        if state:
            lkg = state.get("lkg_version", "0.0.0")
            self.set_target_version(registry_id, device_id, subsystem, lkg)
