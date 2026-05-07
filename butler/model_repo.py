import os
import json

class ModelRepository:
    def __init__(self, file_path=None):
        if file_path is None:
            file_path = os.environ.get("BUTLER_MODEL_FILE", "model.json")
        self.file_path = file_path
        self._load()

    def _load(self):
        if os.path.exists(self.file_path):
            with open(self.file_path, 'r') as f:
                try:
                    self.data = json.load(f)
                except json.JSONDecodeError:
                    self.data = {"registries": {}}
        else:
            self.data = {"registries": {}}
        
        if "registries" not in self.data:
            # Migration: if old format, move to default registry
            if "devices" in self.data:
                self.data = {"registries": {"default": {"devices": self.data["devices"]}}}
            else:
                self.data = {"registries": {}}

    def reload(self):
        self._load()

    def _save(self):
        temp_file = self.file_path + ".tmp"
        with open(temp_file, 'w') as f:
            json.dump(self.data, f, indent=4)
        os.rename(temp_file, self.file_path)

    def _ensure_registry_device(self, registry_id, device_id, subsystem):
        if "registries" not in self.data:
            self.data["registries"] = {}
        if registry_id not in self.data["registries"]:
            self.data["registries"][registry_id] = {"devices": {}}
        if "devices" not in self.data["registries"][registry_id]:
            self.data["registries"][registry_id]["devices"] = {}
        if device_id not in self.data["registries"][registry_id]["devices"]:
            self.data["registries"][registry_id]["devices"][device_id] = {}
        if subsystem not in self.data["registries"][registry_id]["devices"][device_id]:
            self.data["registries"][registry_id]["devices"][device_id][subsystem] = {
                "current_version": "0.0.0",
                "target_version": "0.0.0",
                "lkg_version": "0.0.0"
            }

    def set_device_info(self, registry_id, device_id, subsystem, make, model):
        self._ensure_registry_device(registry_id, device_id, subsystem)
        self.data["registries"][registry_id]["devices"][device_id][subsystem].update({
            "make": make,
            "model": model
        })
        self._save()

    def set_target_version(self, registry_id, device_id, subsystem, version):
        self._ensure_registry_device(registry_id, device_id, subsystem)
        self.data["registries"][registry_id]["devices"][device_id][subsystem]["target_version"] = version
        self._save()

    def update_current_version(self, registry_id, device_id, subsystem, version, lkg_version=None):
        self._ensure_registry_device(registry_id, device_id, subsystem)
        self.data["registries"][registry_id]["devices"][device_id][subsystem]["current_version"] = version
        if lkg_version is not None:
            self.data["registries"][registry_id]["devices"][device_id][subsystem]["lkg_version"] = lkg_version
        self._save()

    def get_device_state(self, registry_id, device_id, subsystem):
        return self.data.get("registries", {}).get(registry_id, {}).get("devices", {}).get(device_id, {}).get(subsystem)

    def get_all_registries(self):
        return self.data.get("registries", {})

    def rollback(self, registry_id, device_id, subsystem):
        state = self.get_device_state(registry_id, device_id, subsystem)
        if state:
            lkg = state.get("lkg_version", "0.0.0")
            self.set_target_version(registry_id, device_id, subsystem, lkg)
