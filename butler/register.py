import sys
import argparse
from butler.model_repo import ModelRepository
from butler.conn_spec import parse_conn_spec

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("registry_id", help="Registry ID")
    parser.add_argument("device_id", help="Device ID")
    args = parser.parse_args()

    print(f"Registering device {args.device_id} in registry {args.registry_id}")
    ModelRepository().set_device_info(args.device_id, "main", "vibrant", "butler-v1")

if __name__ == "__main__":
    main()
