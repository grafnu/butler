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
    device = model.get("devices", {}).get(device_id)

    if not device:
        print(f"Device {device_id} not found in model. Register it first.")
        sys.exit(1)

    make = device.get("make", "default")
    model_name = device.get("model", "default")
    subsystem = "main"

    blob_repo = BlobRepo()
    hash_hex = blob_repo.store_blob(make, model_name, subsystem, blob_version, blob_path)
    print(f"Stored blob with hash {hash_hex}.")

    model_repo.update_target_version(device_id, subsystem, blob_version)
    print(f"Updated target_version to {blob_version} for {device_id} subsystem {subsystem}.")

if __name__ == '__main__':
    main()
