import sys
from butler.model_repo import ModelRepo

def main():
    if len(sys.argv) < 3:
        print("Usage: bin/register registry_id device_id")
        sys.exit(1)

    registry_id = sys.argv[1]
    device_id = sys.argv[2]

    repo = ModelRepo()
    repo.add_device(device_id)
    print(f"Registered device {device_id} in model.")

if __name__ == '__main__':
    main()
