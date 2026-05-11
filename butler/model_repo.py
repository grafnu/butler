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

        # Migration: Flatten subsystems (assuming 'main')
        for reg_id in self.data.get("registries", {}):
            reg_entry = self.data["registries"][reg_id]
            if not isinstance(reg_entry, dict): continue
            devices = reg_entry.get("devices", {})
            for dev_id in list(devices.keys()):
                dev_data = devices[dev_id]
                if isinstance(dev_data, dict) and "main" in dev_data and isinstance(dev_data["main"], dict):
                    # It's in the old nested format
                    devices[dev_id] = dev_data["main"]

    def reload(self):
        self._load()

    def _save(self):
        temp_file = self.file_path + ".tmp"
        with open(temp_file, 'w') as f:
            json.dump(self.data, f, indent=4)
        os.rename(temp_file, self.file_path)

    def _ensure_registry_device(self, registry_id, device_id):
        if "registries" not in self.data:
            self.data["registries"] = {}
        if registry_id not in self.data["registries"]:
            self.data["registries"][registry_id] = {"devices": {}}
        if "devices" not in self.data["registries"][registry_id]:
            self.data["registries"][registry_id]["devices"] = {}
        if device_id not in self.data["registries"][registry_id]["devices"]:
            self.data["registries"][registry_id]["devices"][device_id] = {
                "current_version": "0.0.0",
                "target_version": "0.0.0",
                "lkg_version": "0.0.0",
                "status": "quiescent"
            }

    def set_device_info(self, registry_id, device_id, make, model):
        self._ensure_registry_device(registry_id, device_id)
        self.data["registries"][registry_id]["devices"][device_id].update({
            "make": make,
            "model": model
        })
        self._save()

    def set_target_version(self, registry_id, device_id, version):
        self._ensure_registry_device(registry_id, device_id)
        self.data["registries"][registry_id]["devices"][device_id]["target_version"] = version
        self._save()

    def update_current_version(self, registry_id, device_id, version, lkg_version=None, status=None):
        self._ensure_registry_device(registry_id, device_id)
        self.data["registries"][registry_id]["devices"][device_id]["current_version"] = version
        if lkg_version is not None:
            self.data["registries"][registry_id]["devices"][device_id]["lkg_version"] = lkg_version
        if status is not None:
            self.data["registries"][registry_id]["devices"][device_id]["status"] = status
        self._save()

    def get_device_state(self, registry_id, device_id):
        return self.data.get("registries", {}).get(registry_id, {}).get("devices", {}).get(device_id)

    def get_all_registries(self):
        return self.data.get("registries", {})

    def rollback(self, registry_id, device_id):
        state = self.get_device_state(registry_id, device_id)
        if state:
            lkg = state.get("lkg_version", "0.0.0")
            self.set_target_version(registry_id, device_id, lkg)
