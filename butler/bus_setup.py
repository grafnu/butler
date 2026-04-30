import os
from butler.model_repo import ModelRepository
from butler.blob_repo import BlobRepository

def main():
    print("Setting up Butler environment...")
    project_id = os.environ.get("BUTLER_PROJECT_ID", "vibrant")
    registry_id = os.environ.get("BUTLER_REGISTRY_ID", "controller")
    mqtt_host = os.environ.get("MQTT_HOST", "localhost")
    mqtt_port = os.environ.get("MQTT_PORT", "1883")
    print(f"Project ID: {project_id}")
    print(f"Registry ID: {registry_id}")
    print(f"MQTT Host: {mqtt_host}")
    print(f"MQTT Port: {mqtt_port}")
    
    model_repo = ModelRepository()
    # Clean up model for a fresh start
    model_repo.save_model({})
    
    blob_repo = BlobRepository()
    
    # Initialize some defaults if needed
    if not os.path.exists("testing"):
        os.makedirs("testing")
    
    print("Done.")

if __name__ == "__main__":
    main()
