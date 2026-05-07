import os
import json
import tempfile
import shutil

class ModelRepository:
    def __init__(self, model_file=None):
        self.model_file = model_file or os.environ.get('BUTLER_MODEL_FILE', 'tmp/model.json')
        self._ensure_file()

    def _ensure_file(self):
        if not os.path.exists(self.model_file):
            self.save_model({})

    def load_model(self):
        try:
            with open(self.model_file, 'r') as f:
                data = json.load(f)
                # Migration check
                if data:
                    first_val = next(iter(data.values()))
                    if not isinstance(first_val, dict) or "devices" not in first_val:
                        return {}
                return data
        except (json.JSONDecodeError, FileNotFoundError):
            return {}

    def save_model(self, model):
        # Atomic write
        dir_name = os.path.dirname(os.path.abspath(self.model_file))
        if not os.path.exists(dir_name):
            os.makedirs(dir_name)
        fd, temp_path = tempfile.mkstemp(dir=dir_name)
        try:
            with os.fdopen(fd, 'w') as f:
                json.dump(model, f, indent=2)
            shutil.move(temp_path, self.model_file)
        except Exception as e:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise e

    def get_registry(self, registry_id):
        model = self.load_model()
        return model.get(registry_id, {}).get("devices", {})

    def get_device_subsystems(self, registry_id, device_id):
        reg = self.get_registry(registry_id)
        return reg.get(device_id, {})

    def get_subsystem(self, registry_id, device_id, subsystem_id="main"):
        subsystems = self.get_device_subsystems(registry_id, device_id)
        state = subsystems.get(subsystem_id)
        if not state:
            return {
                "registry_id": registry_id,
                "device_id": device_id,
                "target_version": "1.0",
                "current_version": "1.0",
                "last_known_good": "1.0",
                "state": "quiescent",
                "make": "default",
                "model": "default",
                "subsystem": subsystem_id
            }
        return state

    def save_subsystem(self, registry_id, device_id, subsystem_id, data):
        model = self.load_model()
        if registry_id not in model:
            model[registry_id] = {"devices": {}}
        if "devices" not in model[registry_id]:
            model[registry_id]["devices"] = {}
        if device_id not in model[registry_id]["devices"]:
            model[registry_id]["devices"][device_id] = {}
        model[registry_id]["devices"][device_id][subsystem_id] = data
        self.save_model(model)
        return data

    def update_subsystem(self, registry_id, device_id, subsystem_id, **kwargs):
        state = self.get_subsystem(registry_id, device_id, subsystem_id)
        state.update(kwargs)
        return self.save_subsystem(registry_id, device_id, subsystem_id, state)

    def set_device_info(self, registry_id, device_id, make, model, subsystem):
        kwargs = {"make": make, "model": model, "subsystem": subsystem}
        return self.update_subsystem(registry_id, device_id, subsystem, **kwargs)

    def set_target_version(self, registry_id, device_id, version, subsystem="main"):
        return self.update_subsystem(registry_id, device_id, subsystem, target_version=version)

    def register_device(self, registry_id, device_id, subsystem="main"):
        return self.update_subsystem(registry_id, device_id, subsystem)
