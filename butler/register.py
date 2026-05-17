import sys
import argparse
from butler.model_repo import ModelRepo
from butler.transport import parse_conn_spec

def main():

    parser = argparse.ArgumentParser()
    parser.add_argument("--conn_spec", help="Connection spec")
    parser.add_argument("args", nargs="*", help="Arguments")
    args_obj, unknown = parser.parse_known_args()
    
    args = args_obj.args
    conn_spec_str = args_obj.conn_spec
    if not conn_spec_str and args and ("://" in args[0] or args[0].startswith("localhost")):
        conn_spec_str = args.pop(0)
    
    if not conn_spec_str:
        from butler.transport import get_default_conn_spec
        conn_spec_str = get_default_conn_spec()

    if len(args) < 2:
        print("Usage: bin/register [conn_spec] registry_id device_id")
        sys.exit(1)
    
    registry_id = args[0]
    device_id = args[1]

    repo = ModelRepo()
    repo.add_device(registry_id, device_id)
    print(f"Registered device {device_id} in registry {registry_id} in model.")

if __name__ == '__main__':
    main()
