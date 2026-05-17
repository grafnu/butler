import hashlib
import os
import shutil

class BlobRepo:
    def __init__(self, base_dir="testing/blobs"):
        self.base_dir = base_dir

    def store_blob(self, make: str, model: str, subsystem: str, version: str, blob_path: str) -> str:
        sha256_hash = hashlib.sha256()
        with open(blob_path, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        hash_hex = sha256_hash.hexdigest()

        target_dir = os.path.join(self.base_dir, make, model, subsystem, version)
        os.makedirs(target_dir, exist_ok=True)

        target_path = os.path.join(target_dir, "bundle.bin")
        shutil.copy2(blob_path, target_path)

        with open(os.path.join(target_dir, "sha256.txt"), "w") as f:
            f.write(hash_hex)

        return hash_hex

    def get_blob_info(self, make: str, model: str, subsystem: str, version: str) -> dict:
        target_dir = os.path.join(self.base_dir, make, model, subsystem, version)
        hash_file = os.path.join(target_dir, "sha256.txt")

        if not os.path.exists(hash_file):
            return None

        with open(hash_file, "r") as f:
            hash_hex = f.read().strip()

        blob_path = os.path.join(target_dir, "bundle.bin")
        if not os.path.exists(blob_path):
            return None

        return {
            "hash": hash_hex,
            "path": blob_path,
            "url": f"file://{os.path.abspath(blob_path)}"
        }
