import os
import hashlib
import json
import sys

class BlobRepository:
    def __init__(self, base_dir=None):
        if base_dir is None:
            # ASSUMPTION: Default BUTLER_BLOBS_DIR is udmi_blob_store/packages per spec/blobstore.md Section 1.2
            base_dir = os.environ.get("BUTLER_BLOBS_DIR", "udmi_blob_store/packages")
        workspace_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if not os.path.isabs(base_dir):
            base_dir = os.path.abspath(os.path.join(workspace_root, base_dir))
        self.base_dir = base_dir
        os.makedirs(self.base_dir, exist_ok=True)

    def store_blob(self, make, model, subsystem, version, data):
        target_dir = os.path.join(self.base_dir, make, model, subsystem, version)
        os.makedirs(target_dir, exist_ok=True)
        
        blob_path = os.path.join(target_dir, "bundle.bin")
        with open(blob_path, "wb") as f:
            f.write(data)
        
        sha256 = hashlib.sha256(data).hexdigest()
        hash_path = os.path.join(target_dir, "sha256.txt")
        with open(hash_path, "w") as f:
            f.write(sha256)
        
        return blob_path, sha256

    def get_blob_metadata(self, make, model, subsystem, version):
        # ASSUMPTION: Sourced from BUTLER_MODEL_FILE per spec/butler.md Section 5.5.
        # Dynamically queries model.json file on disk for every metadata/package query to prevent out-of-sync cache errors.
        model_file = os.environ.get("BUTLER_MODEL_FILE", "udmi_blob_store/model.json")
        workspace_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        abs_model_file = model_file if os.path.isabs(model_file) else os.path.abspath(os.path.join(workspace_root, model_file))
        
        url = None
        if os.path.exists(abs_model_file):
            try:
                with open(abs_model_file, "r") as f:
                    catalog = json.load(f)
                entry = catalog.get(make, {}).get(model, {}).get(subsystem, {}).get(version, {})
                if isinstance(entry, dict):
                    url = entry.get("url")
            except Exception as e:
                sys.stderr.write(f"[blob_repo] Error reading model file {abs_model_file}: {e}\n")
        
        # Resolve via Software Catalog URL if present
        if url:
            resolved_path = url
            if url.startswith("file://"):
                resolved_path = url[7:]
            # Resolve relative paths relative to workspace root per spec/blobstore.md Section 2.1.2
            if not os.path.isabs(resolved_path):
                resolved_path = os.path.abspath(os.path.join(workspace_root, resolved_path))
            
            if os.path.exists(resolved_path):
                # Try to read sha256.txt from the same directory if it exists
                dir_name = os.path.dirname(resolved_path)
                sha_file = os.path.join(dir_name, "sha256.txt")
                sha256 = None
                if os.path.exists(sha_file):
                    try:
                        with open(sha_file, "r") as f:
                            sha256 = f.read().strip()
                    except Exception:
                        pass
                
                # Calculate sha256 dynamically if missing
                if not sha256:
                    try:
                        with open(resolved_path, "rb") as f:
                            sha256 = hashlib.sha256(f.read()).hexdigest()
                    except Exception as e:
                        sys.stderr.write(f"[blob_repo] Error hashing file {resolved_path}: {e}\n")
                        return None
                
                return {
                    "url": url,
                    "sha256": sha256
                }
        
        # Fallback: look in directory structure under self.base_dir
        target_dir = os.path.join(self.base_dir, make, model, subsystem, version)
        blob_path = None
        for name in ["bundle.bin", "bundle.txt"]:
            p = os.path.join(target_dir, name)
            if os.path.exists(p):
                blob_path = p
                break
        
        if blob_path:
            sha_file = os.path.join(target_dir, "sha256.txt")
            sha256 = None
            if os.path.exists(sha_file):
                try:
                    with open(sha_file, "r") as f:
                        sha256 = f.read().strip()
                except Exception:
                    pass
            if not sha256:
                try:
                    with open(blob_path, "rb") as f:
                        sha256 = hashlib.sha256(f.read()).hexdigest()
                except Exception:
                    return None
            
            rel_path = os.path.relpath(blob_path, workspace_root)
            return {
                "url": f"file://{rel_path}",
                "sha256": sha256
            }
            
        return None
