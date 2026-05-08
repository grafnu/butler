import json
import os
import tempfile

class ModelRepo:
    def __init__(self):
        self.model_file = os.environ.get("BUTLER_MODEL_FILE", "testing/model.json")
        self._ensure_exists()

    def _ensure_exists(self):
        if not os.path.exists(self.model_file):
            os.makedirs(os.path.dirname(os.path.abspath(self.model_file)), exist_ok=True)
            self._write_model({"registries": {}})
        else:
            # Migration/Normalization: ensure 'registries' key exists
            data = self._read_model()
            if "registries" not in data:
                if "devices" in data:
                    data = {"registries": {"default": {"devices": data["devices"]}}}
                else:
                    data["registries"] = {}
                self._write_model(data)

    def _read_model(self) -> dict:
        if not os.path.exists(self.model_file):
            return {"registries": {}}
        with open(self.model_file, "r") as f:
            try:
                data = json.load(f)
                if "registries" not in data:
                    return {"registries": {}}
                return data
            except json.JSONDecodeError:
                return {"registries": {}}

    def _write_model(self, data: dict):
        fd, temp_path = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(self.model_file)))
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(temp_path, self.model_file)

    def get_model(self) -> dict:
        return self._read_model()

    def add_device(self, registry_id: str, device_id: str, make="default", model="default"):
        data = self._read_model()
        reg = data["registries"].setdefault(registry_id, {"devices": {}})
        devices = reg.setdefault("devices", {})
        if device_id not in devices:
            devices[device_id] = {
                "make": make,
                "model": model,
                "subsystems": {}
            }
            self._write_model(data)

    def update_target_version(self, registry_id: str, device_id: str, subsystem: str, target_version: str):
        data = self._read_model()
        reg = data["registries"].setdefault(registry_id, {"devices": {}})
        devices = reg.setdefault("devices", {})
        device = devices.setdefault(device_id, {"subsystems": {}})
        subs = device.setdefault("subsystems", {})
        sub = subs.setdefault(subsystem, {})

        sub["target_version"] = target_version
        self._write_model(data)

    def update_current_version(self, registry_id: str, device_id: str, subsystem: str, current_version: str):
        data = self._read_model()
        reg = data["registries"].setdefault(registry_id, {"devices": {}})
        devices = reg.setdefault("devices", {})
        device = devices.setdefault(device_id, {"subsystems": {}})
        subs = device.setdefault("subsystems", {})
        sub = subs.setdefault(subsystem, {})

        sub["current_version"] = current_version
        sub["lkg_version"] = current_version
        self._write_model(data)

    def revert_to_lkg(self, registry_id: str, device_id: str, subsystem: str):
        data = self._read_model()
        reg = data["registries"].get(registry_id, {})
        devices = reg.get("devices", {})
        device = devices.get(device_id, {}).get("subsystems", {}).get(subsystem)
        if device and "lkg_version" in device:
            device["target_version"] = device["lkg_version"]
            self._write_model(data)
