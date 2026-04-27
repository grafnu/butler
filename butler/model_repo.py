import os
import json
import tempfile

class ModelRepository:
    def __init__(self, model_file=None):
        if model_file is None:
            model_file = os.environ.get("BUTLER_MODEL_FILE", "model.json")
        self.model_file = model_file
        self.data = self._load()

    def _load(self):
        if os.path.exists(self.model_file):
            try:
                with open(self.model_file, "r") as f:
                    return json.load(f)
            except json.JSONDecodeError:
                return {}
        return {}

    def _save(self):
        # Atomic write
        dir_name = os.path.dirname(os.path.abspath(self.model_file))
        os.makedirs(dir_name, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(dir=dir_name, prefix="model_", suffix=".json.tmp")
        try:
            with os.fdopen(fd, 'w') as f:
                json.dump(self.data, f, indent=4)
            os.replace(temp_path, self.model_file)
        except Exception as e:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise e

    def set_device_info(self, device_id, make, model, subsystem):
        if device_id not in self.data:
            self.data[device_id] = {}
        self.data[device_id].update({
            "make": make,
            "model": model,
            "subsystem": subsystem
        })
        self._save()

    def set_target_version(self, device_id, version):
        if device_id not in self.data:
            self.data[device_id] = {}
        self.data[device_id]["target_version"] = version
        self._save()

    def update_current_version(self, device_id, version):
        if device_id not in self.data:
            self.data[device_id] = {}
        
        # If update is successful, the previous current_version becomes last_known_good
        old_current = self.data[device_id].get("current_version")
        if old_current and old_current != version:
            self.data[device_id]["last_known_good"] = old_current
            
        self.data[device_id]["current_version"] = version
        self._save()

    def get_device_state(self, device_id):
        return self.data.get(device_id)

    def get_all_devices(self):
        return self.data.keys()
