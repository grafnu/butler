import os
from butler.model_repo import ModelRepository
from butler.blob_repo import BlobRepository

def main():
    print("Setting up Butler environment...")
    project_id = os.environ.get("BUTLER_PROJECT_ID", "vibrant")
    registry_id = os.environ.get("BUTLER_REGISTRY_ID", "controller")
    print(f"Project ID: {project_id}")
    print(f"Registry ID: {registry_id}")
    
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
