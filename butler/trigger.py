import sys
from butler.model_repo import ModelRepo
from butler.blob_repo import BlobRepo

def main():
    if len(sys.argv) < 5:
        print("Usage: bin/trigger registry_id device_id blob_version blob_path")
        sys.exit(1)

    registry_id = sys.argv[1]
    device_id = sys.argv[2]
    blob_version = sys.argv[3]
    blob_path = sys.argv[4]

    model_repo = ModelRepo()
    model = model_repo.get_model()
    reg = model.get("registries", {}).get(registry_id, {})
    device = reg.get("devices", {}).get(device_id)

    if not device:
        print(f"Device {device_id} not found in registry {registry_id} in model. Register it first.")
        sys.exit(1)

    make = device.get("make", "default")
    model_name = device.get("model", "default")

    blob_repo = BlobRepo()
    hash_hex = blob_repo.store_blob(make, model_name, "main", blob_version, blob_path) # Blobs might still need subsystem folder locally, or we can just use "main"
    print(f"Stored blob with hash {hash_hex}.")

    model_repo.update_target_version(registry_id, device_id, blob_version)
    print(f"Updated target_version to {blob_version} for {device_id} in {registry_id}.")

if __name__ == '__main__':
    main()
