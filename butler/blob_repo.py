import os
import hashlib

class BlobRepository:
    def __init__(self, base_dir="blobs"):
        self.base_dir = base_dir
        if not os.path.exists(self.base_dir):
            os.makedirs(self.base_dir)

    def get_blob_path(self, make, model, subsystem, version):
        return os.path.join(self.base_dir, make, model, subsystem, version, "bundle.bin")

    def store_blob(self, make, model, subsystem, version, data):
        path = self.get_blob_path(make, model, subsystem, version)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f:
            f.write(data)
        
        sha256 = hashlib.sha256(data).hexdigest()
        with open(path + ".sha256", 'w') as f:
            f.write(sha256)
        
        return path, sha256

    def get_blob_metadata(self, make, model, subsystem, version):
        path = self.get_blob_path(make, model, subsystem, version)
        if not os.path.exists(path):
            return None
        
        sha256_path = path + ".sha256"
        if os.path.exists(sha256_path):
            with open(sha256_path, 'r') as f:
                sha256 = f.read().strip()
        else:
            with open(path, 'rb') as f:
                sha256 = hashlib.sha256(f.read()).hexdigest()
        
        return {
            "path": os.path.abspath(path),
            "sha256": sha256,
            "url": f"file://{os.path.abspath(path)}"
        }
