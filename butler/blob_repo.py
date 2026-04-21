import os
import hashlib
import shutil
import json

class BlobRepository:
    def __init__(self, root_dir="blobs"):
        self.root_dir = root_dir
        os.makedirs(self.root_dir, exist_ok=True)

    def get_path(self, make, model, subsystem, version):
        return os.path.join(self.root_dir, make, model, subsystem, version)

    def store_blob(self, make, model, subsystem, version, content):
        path = self.get_path(make, model, subsystem, version)
        os.makedirs(path, exist_ok=True)
        file_path = os.path.join(path, "firmware.bin")
        with open(file_path, "wb") as f:
            f.write(content)
        
        sha256_hash = self.get_sha256(content)
        with open(os.path.join(path, "firmware.sha256"), "w") as f:
            f.write(sha256_hash)
        
        return file_path, sha256_hash

    def get_sha256(self, content):
        return hashlib.sha256(content).hexdigest()

    def get_blob_info(self, make, model, subsystem, version):
        path = self.get_path(make, model, subsystem, version)
        file_path = os.path.join(path, "firmware.bin")
        sha256_path = os.path.join(path, "firmware.sha256")
        
        if not os.path.exists(file_path):
            return None
        
        with open(sha256_path, "r") as f:
            sha256 = f.read().strip()
        
        return {
            "url": f"file://{os.path.abspath(file_path)}",
            "sha256": sha256
        }

if __name__ == "__main__":
    import sys
    import argparse
    
    parser = argparse.ArgumentParser(description="Blob Repository CLI")
    parser.add_argument("--root", default="blobs", help="Root directory for blobs")
    subparsers = parser.add_argument_group("Commands").add_subparsers(dest="command")
    
    # Store command
    store_parser = subparsers.add_parser("store", help="Store a blob")
    store_parser.add_argument("--make", required=True)
    store_parser.add_argument("--model", required=True)
    store_parser.add_argument("--subsystem", required=True)
    store_parser.add_argument("--version", required=True)
    store_parser.add_argument("--file", required=True, help="Path to the binary file")
    
    # Info command
    info_parser = subparsers.add_parser("info", help="Get info for a blob")
    info_parser.add_argument("--make", required=True)
    info_parser.add_argument("--model", required=True)
    info_parser.add_argument("--subsystem", required=True)
    info_parser.add_argument("--version", required=True)
    
    args = parser.parse_args()
    repo = BlobRepository(args.root)
    
    if args.command == "store":
        with open(args.file, "rb") as f:
            content = f.read()
        path, sha256 = repo.store_blob(args.make, args.model, args.subsystem, args.version, content)
        print(f"Stored blob at {path}")
        print(f"SHA256: {sha256}")
    elif args.command == "info":
        info = repo.get_blob_info(args.make, args.model, args.subsystem, args.version)
        if info:
            print(json.dumps(info, indent=2))
        else:
            print("Blob not found")
            sys.exit(1)
    else:
        parser.print_help()
