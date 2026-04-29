import os
import json
import tempfile
import shutil

class ModelRepository:
    def __init__(self, model_file=None):
        self.model_file = model_file or os.environ.get('BUTLER_MODEL_FILE', 'model.json')
        self._ensure_file()

    def _ensure_file(self):
        if not os.path.exists(self.model_file):
            self.save_model({})

    def load_model(self):
        try:
            with open(self.model_file, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return {}

    def save_model(self, model):
        # Atomic write
        fd, temp_path = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(self.model_file)))
        try:
            with os.fdopen(fd, 'w') as f:
                json.dump(model, f, indent=2)
            shutil.move(temp_path, self.model_file)
        except Exception as e:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise e

    def get_device_state(self, device_id):
        model = self.load_model()
        return model.get(device_id)

    def get_device(self, device_id):
        state = self.get_device_state(device_id)
        if not state:
            return {
                "target_version": "1.0",
                "current_version": "1.0",
                "last_known_good": "1.0",
                "state": "quiescent",
                "make": "default",
                "model": "default",
                "subsystem": "default"
            }
        return state

    def update_device(self, device_id, **kwargs):
        model = self.load_model()
        device = model.get(device_id, {
            "target_version": "1.0",
            "current_version": "1.0",
            "last_known_good": "1.0",
            "state": "quiescent",
            "make": "default",
            "model": "default",
            "subsystem": "default"
        })
        device.update(kwargs)
        model[device_id] = device
        self.save_model(model)
        return device

    def set_device_info(self, device_id, make, model, subsystem):
        return self.update_device(device_id, make=make, model=model, subsystem=subsystem)

    def set_target_version(self, device_id, version):
        return self.update_device(device_id, target_version=version)

    def register_device(self, device_id):
        return self.update_device(device_id)
