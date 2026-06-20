import os
import hashlib
import json

class LocalBlobStore:
    def __init__(self, model_file=None, blobs_dir=None):
        self.model_file = model_file or os.environ.get("BUTLER_MODEL_FILE", "udmi_blob_store/model.json")
        self.blobs_dir = blobs_dir or os.environ.get("BUTLER_BLOBS_DIR", "udmi_blob_store/packages")
        
    def resolve_package_metadata(self, make, model, blob_id, version):
        # Always reload on every query to prevent caching out-of-sync issues
        url = None
        if os.path.exists(self.model_file):
            try:
                with open(self.model_file, "r") as f:
                    data = json.load(f)
                url = data[make][model][blob_id][version]["url"]
            except Exception:
                pass
                
        # If url is not resolved from model.json, use standard directory layout path
        if not url:
            # Check directory layout: {blobs_dir}/{make}/{model}/{blob_id}/{version}/
            path = os.path.join(self.blobs_dir, make, model, blob_id, version)
            bundle_file = None
            for fname in ["bundle.bin", "bundle.txt"]:
                fpath = os.path.join(path, fname)
                if os.path.exists(fpath):
                    bundle_file = fpath
                    break
            if bundle_file:
                url = f"file://{bundle_file}"
            else:
                raise FileNotFoundError(f"No package bundle found for {make}/{model}/{blob_id}/{version}")
                
        # Resolve physical file path from url
        if url.startswith("file://"):
            file_path = url[7:]
        else:
            file_path = url
            
        # Resolve relative to workspace root (the current directory)
        workspace_root = os.getcwd()
        abs_path = os.path.abspath(os.path.join(workspace_root, file_path))
        
        # Fallback search if file doesn't exist at resolved path
        if not os.path.exists(abs_path):
            fallback_dir = os.path.join(self.blobs_dir, make, model, blob_id, version)
            for fname in ["bundle.bin", "bundle.txt"]:
                fpath = os.path.join(fallback_dir, fname)
                if os.path.exists(fpath):
                    abs_path = os.path.abspath(fpath)
                    url = f"file://{fpath}"
                    break
                    
        if not os.path.exists(abs_path):
            raise FileNotFoundError(f"Package bundle file not found at: {abs_path}")
            
        # Compute SHA-256 dynamically
        sha256_hash = hashlib.sha256()
        with open(abs_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                sha256_hash.update(chunk)
                
        return {
            "sha256": sha256_hash.hexdigest(),
            "url": url
        }

class GCSBlobStore:
    def __init__(self, bucket_name=None, creds_path=None):
        self.bucket_name = bucket_name or os.environ.get("BUTLER_GCS_BUCKET")
        self.creds_path = creds_path or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        
    def resolve_package_metadata(self, make, model, blob_id, version):
        from google.cloud import storage
        import datetime
        
        if self.creds_path:
            client = storage.Client.from_service_account_json(self.creds_path)
        else:
            client = storage.Client()
            
        bucket = client.bucket(self.bucket_name)
        # Suffix matching layout
        blob_name = f"{make}/{model}/{blob_id}/{version}/bundle.bin"
        blob = bucket.blob(blob_name)
        
        blob.reload()
        # Extract custom metadata key
        sha256 = blob.metadata.get("sha256") or blob.metadata.get("x-goog-meta-sha256")
        
        url = blob.generate_signed_url(
            version="v4",
            expiration=datetime.timedelta(minutes=15),
            method="GET"
        )
        
        return {
            "sha256": sha256,
            "url": url
        }

def get_blobstore_provider():
    provider_type = os.environ.get("BUTLER_BLOBSTORE_PROVIDER", "local").lower()
    if provider_type == "gcs":
        return GCSBlobStore()
    else:
        return LocalBlobStore()
