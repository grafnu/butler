import sys
import argparse
from butler.model_repo import ModelRepository
from butler.conn_spec import parse_conn_spec

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("conn_spec", help="Connection spec URL")
    parser.add_argument("device_id")
    args = parser.parse_args()

    print(f"Registering device {args.device_id} for conn_spec {args.conn_spec}")
    ModelRepository().set_device_info(args.device_id, "main", "vibrant", "butler-v1")

if __name__ == "__main__":
    main()
