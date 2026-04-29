import os
import hashlib
import shutil

class BlobRepository:
    def __init__(self, base_path=None):
        if base_path is None:
            base_path = os.environ.get("BUTLER_BLOB_DIR", "blobs")
        self.base_path = base_path

    def _get_path(self, make, model, subsystem, version):
        return os.path.join(self.base_path, make, model, subsystem, version)

    def store_blob(self, make, model, subsystem, version, content):
        dir_path = self._get_path(make, model, subsystem, version)
        os.makedirs(dir_path, exist_ok=True)
        
        blob_path = os.path.join(dir_path, "blob.bin")
        with open(blob_path, "wb") as f:
            f.write(content)
        
        sha256_hash = hashlib.sha256(content).hexdigest()
        sha_path = os.path.join(dir_path, "blob.sha256")
        with open(sha_path, "w") as f:
            f.write(sha256_hash)
            
        return blob_path, sha256_hash

    def get_blob_info(self, make, model, subsystem, version):
        dir_path = self._get_path(make, model, subsystem, version)
        blob_path = os.path.join(dir_path, "blob.bin")
        sha_path = os.path.join(dir_path, "blob.sha256")
        
        if not os.path.exists(blob_path):
            return None, None
            
        with open(sha_path, "r") as f:
            sha256_hash = f.read().strip()
            
        return blob_path, sha256_hash
