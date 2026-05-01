import os
import hashlib

class BlobRepository:
    def __init__(self, base_dir=None):
        if base_dir is None:
            base_dir = os.environ.get("BUTLER_BLOBS_DIR", "testing/blobs")
        self.base_dir = base_dir
        if not os.path.exists(self.base_dir):
            os.makedirs(self.base_dir)

    def store_blob(self, make, model, subsystem, version, data):
        target_dir = os.path.join(self.base_dir, make, model, subsystem, version)
        if not os.path.exists(target_dir):
            os.makedirs(target_dir)
        
        blob_path = os.path.join(target_dir, "bundle.bin")
        with open(blob_path, "wb") as f:
            f.write(data)
        
        sha256 = hashlib.sha256(data).hexdigest()
        hash_path = os.path.join(target_dir, "sha256.txt")
        with open(hash_path, "w") as f:
            f.write(sha256)
        
        return blob_path, sha256

    def get_blob_metadata(self, make, model, subsystem, version):
        target_dir = os.path.join(self.base_dir, make, model, subsystem, version)
        blob_path = os.path.join(target_dir, "bundle.bin")
        hash_path = os.path.join(target_dir, "sha256.txt")
        
        if os.path.exists(blob_path) and os.path.exists(hash_path):
            with open(hash_path, "r") as f:
                sha256 = f.read().strip()
            return {
                "url": os.path.abspath(blob_path),
                "sha256": sha256
            }
        return None
