import os
import json
import shutil

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
                    self.data = {"devices": {}}
        else:
            self.data = {"devices": {}}

    def reload(self):
        self._load()

    def _save(self):
        temp_file = self.file_path + ".tmp"
        with open(temp_file, 'w') as f:
            json.dump(self.data, f, indent=4)
        os.rename(temp_file, self.file_path)

    def set_device_info(self, device_id, subsystem, make, model):
        if "devices" not in self.data:
            self.data["devices"] = {}
        if device_id not in self.data["devices"]:
            self.data["devices"][device_id] = {}
        if subsystem not in self.data["devices"][device_id]:
            self.data["devices"][device_id][subsystem] = {
                "current_version": "0.0.0",
                "target_version": "0.0.0",
                "lkg_version": "0.0.0"
            }
        self.data["devices"][device_id][subsystem].update({
            "make": make,
            "model": model
        })
        self._save()

    def set_target_version(self, device_id, subsystem, version):
        if device_id in self.data.get("devices", {}) and subsystem in self.data["devices"][device_id]:
            self.data["devices"][device_id][subsystem]["target_version"] = version
            self._save()

    def update_current_version(self, device_id, subsystem, version):
        if device_id in self.data.get("devices", {}) and subsystem in self.data["devices"][device_id]:
            current = self.data["devices"][device_id][subsystem]["current_version"]
            if current != version and current != "0.0.0":
                self.data["devices"][device_id][subsystem]["lkg_version"] = current
            self.data["devices"][device_id][subsystem]["current_version"] = version
            self._save()

    def get_device_state(self, device_id, subsystem):
        return self.data.get("devices", {}).get(device_id, {}).get(subsystem)

    def get_all_devices(self):
        return self.data.get("devices", {})

    def rollback(self, device_id, subsystem):
        if device_id in self.data.get("devices", {}) and subsystem in self.data["devices"][device_id]:
            lkg = self.data["devices"][device_id][subsystem].get("lkg_version", "0.0.0")
            print(f"Rolling back {device_id}/{subsystem} to {lkg}")
            self.data["devices"][device_id][subsystem]["target_version"] = lkg
            self._save()
