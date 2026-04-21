import json
import os

class ModelRepository:
    def __init__(self, model_file=None):
        if model_file is None:
            model_file = os.environ.get("BUTLER_MODEL_FILE", "model.json")
        self.model_file = model_file
        self.data = self._load()

    def _load(self):
        if os.path.exists(self.model_file):
            try:
                with open(self.model_file, "r") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                pass
        return {"devices": {}}

    def _save(self):
        with open(self.model_file, "w") as f:
            json.dump(self.data, f, indent=4)

    def get_device_state(self, device_id, subsystem="main"):
        self.data = self._load()
        device = self.data["devices"].get(device_id, {})
        return device.get(subsystem)

    def set_target_version(self, device_id, subsystem, version):
        self.data = self._load()
        if device_id not in self.data["devices"]:
            self.data["devices"][device_id] = {}
        
        if subsystem not in self.data["devices"][device_id]:
             self.data["devices"][device_id][subsystem] = {
                 "current_version": "0.0.0",
                 "target_version": version,
                 "last_known_good": "0.0.0",
                 "make": "unknown",
                 "model": "unknown"
             }
        else:
             self.data["devices"][device_id][subsystem]["target_version"] = version
        
        self._save()

    def update_current_version(self, device_id, subsystem, version):
        self.data = self._load()
        if device_id in self.data["devices"] and subsystem in self.data["devices"][device_id]:
            sub = self.data["devices"][device_id][subsystem]
            if sub["current_version"] != version:
                 # If the new version is confirmed, it becomes the new LKG
                 sub["current_version"] = version
                 sub["last_known_good"] = version
                 self._save()

    def set_device_info(self, device_id, subsystem, make, model):
        self.data = self._load()
        if device_id not in self.data["devices"]:
            self.data["devices"][device_id] = {}
        
        if subsystem not in self.data["devices"][device_id]:
             self.data["devices"][device_id][subsystem] = {
                 "current_version": "0.0.0",
                 "target_version": "0.0.0",
                 "last_known_good": "0.0.0",
             }
        
        self.data["devices"][device_id][subsystem]["make"] = make
        self.data["devices"][device_id][subsystem]["model"] = model
        self._save()

    def get_all_mismatches(self):
        self.data = self._load()
        mismatches = []
        for device_id, subsystems in self.data["devices"].items():
            for subsystem, state in subsystems.items():
                if state["current_version"] != state["target_version"]:
                    mismatches.append({
                        "device_id": device_id,
                        "subsystem": subsystem,
                        "state": state
                    })
        return mismatches

    def rollback(self, device_id, subsystem):
        self.data = self._load()
        if device_id in self.data["devices"] and subsystem in self.data["devices"][device_id]:
            sub = self.data["devices"][device_id][subsystem]
            print(f"Rolling back {device_id}/{subsystem} to {sub['last_known_good']}")
            sub["target_version"] = sub["last_known_good"]
            self._save()

if __name__ == "__main__":
    import sys
    import argparse
    import json
    
    parser = argparse.ArgumentParser(description="Model Repository CLI")
    parser.add_argument("--model-file", default="model.json", help="Model file path")
    subparsers = parser.add_subparsers(dest="command")
    
    # Init command
    init_parser = subparsers.add_parser("init", help="Initialize a device")
    init_parser.add_argument("--device", required=True)
    init_parser.add_argument("--subsystem", default="main")
    init_parser.add_argument("--make", required=True)
    init_parser.add_argument("--model", required=True)
    
    # Set target command
    target_parser = subparsers.add_parser("set-target", help="Set target version for a device")
    target_parser.add_argument("--device", required=True)
    target_parser.add_argument("--subsystem", default="main")
    target_parser.add_argument("--version", required=True)
    
    # List command
    list_parser = subparsers.add_parser("list", help="List all devices and their states")
    
    args = parser.parse_args()
    repo = ModelRepository(args.model_file)
    
    if args.command == "init":
        repo.set_device_info(args.device, args.subsystem, args.make, args.model)
        print(f"Initialized {args.device}/{args.subsystem}")
    elif args.command == "set-target":
        repo.set_target_version(args.device, args.subsystem, args.version)
        print(f"Set {args.device}/{args.subsystem} target to {args.version}")
    elif args.command == "list":
        print(json.dumps(repo.data, indent=2))
    elif args.command:
        pass
    else:
        parser.print_help()
