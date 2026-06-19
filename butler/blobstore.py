import os
import json
import hashlib
from urllib.parse import urlparse

class PackageMetadata:
    def __init__(self, sha256, url):
        self.sha256 = sha256
        self.url = url

    def to_dict(self):
        return {
            "sha256": self.sha256,
            "url": self.url
        }

def get_workspace_root():
    # Sourced relative to this file
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

class LocalBlobStore:
    def __init__(self):
        self.blobs_dir = os.environ.get("BUTLER_BLOBS_DIR", "udmi_blob_store/packages")
        self.model_file = os.environ.get("BUTLER_MODEL_FILE", "udmi_blob_store/model.json")

    def _get_absolute_path(self, path):
        if path.startswith("file://"):
            path = path[7:]
        # Resolve relative to workspace root
        if not os.path.isabs(path):
            path = os.path.join(get_workspace_root(), path)
        return os.path.abspath(path)

    def _calculate_sha256(self, file_path):
        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()

    def _load_model(self):
        model_path = self._get_absolute_path(self.model_file)
        if not os.path.exists(model_path):
            raise FileNotFoundError(f"Model file not found at {model_path}")
        with open(model_path, "r") as f:
            return json.load(f)

    def resolve_package_metadata(self, make, model, blob_id, version):
        catalog = self._load_model()
        try:
            package_info = catalog[make][model][blob_id][version]
            raw_url = package_info["url"]
        except KeyError:
            raise ValueError(f"No package metadata found for {make}/{model}/{blob_id}/{version}")

        # Resolve path to bundle to calculate hash
        # If url is file://, resolve it and calculate hash of the file.
        # If url is relative, we can try to find the bundle.bin or bundle.txt under packages/ as a fallback
        resolved_path = self._get_absolute_path(raw_url)
        if not os.path.exists(resolved_path):
            # Fallback to default directory mapping: {BUTLER_BLOBS_DIR}/{make}/{model}/{blob_id}/{version}/
            fallback_dir = os.path.join(
                self._get_absolute_path(self.blobs_dir),
                make, model, blob_id, version
            )
            bin_path = os.path.join(fallback_dir, "bundle.bin")
            txt_path = os.path.join(fallback_dir, "bundle.txt")
            if os.path.exists(bin_path):
                resolved_path = bin_path
            elif os.path.exists(txt_path):
                resolved_path = txt_path
            else:
                raise FileNotFoundError(f"Bundle file not found at {resolved_path} or fallback paths.")

        sha256 = self._calculate_sha256(resolved_path)
        
        # Ensure returned URL is standard file:// format
        # If the model JSON contains a relative path, we want the client to receive it.
        # But AGENTS.md says: "All components MUST resolve relative file:// paths defined in the Software Catalog (model.json) relative to the project workspace root directory, regardless of which subdirectory they are executed from."
        # So we can just return the raw URL or absolute URL. Let's return raw_url (or file:// relative)
        return PackageMetadata(sha256, raw_url)

    def get_package_url(self, make, model, blob_id, version):
        meta = self.resolve_package_metadata(make, model, blob_id, version)
        return meta.url


class GcsBlobStore:
    def __init__(self):
        self.bucket_name = os.environ.get("BUTLER_GCS_BUCKET")
        if not self.bucket_name:
            raise ValueError("BUTLER_GCS_BUCKET environment variable must be set for GCS provider")

    def _get_client(self):
        try:
            from google.cloud import storage
            return storage.Client()
        except ImportError:
            raise ImportError("google-cloud-storage package is required for GCS provider")

    def resolve_package_metadata(self, make, model, blob_id, version):
        client = self._get_client()
        bucket = client.bucket(self.bucket_name)
        
        blob_path = f"{make}/{model}/{blob_id}/{version}/bundle.bin"
        blob = bucket.blob(blob_path)
        
        if not blob.exists():
            raise FileNotFoundError(f"GCS object gs://{self.bucket_name}/{blob_path} not found")

        # Reload metadata
        blob.reload()
        sha256 = blob.metadata.get("sha256") if blob.metadata else None
        if not sha256:
            # Maybe check alternative metadata headers
            sha256 = blob.metadata.get("x-goog-meta-sha256") if blob.metadata else None
            
        if not sha256:
            raise ValueError(f"sha256 metadata is missing on GCS object gs://{self.bucket_name}/{blob_path}")

        # Generate a time-limited Signed URL (valid for 15 minutes)
        import datetime
        url = blob.generate_signed_url(
            version="v4",
            expiration=datetime.timedelta(minutes=15),
            method="GET"
        )
        return PackageMetadata(sha256, url)

    def get_package_url(self, make, model, blob_id, version):
        meta = self.resolve_package_metadata(make, model, blob_id, version)
        return meta.url


def get_blobstore_provider():
    provider_name = os.environ.get("BUTLER_BLOBSTORE_PROVIDER", "local").lower()
    if provider_name == "gcs":
        return GcsBlobStore()
    else:
        return LocalBlobStore()
