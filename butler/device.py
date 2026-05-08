import time
import argparse
import threading
import hashlib
import os
from butler.common import ButlerBusFactory, get_default_conn_spec
from butler.model_repo import ModelRepository

class MocketDevice:
    def __init__(self, registry_id, device_id, conn_spec=None, failure_mode=False):
        conn_spec = conn_spec or get_default_conn_spec()
        self.bus = ButlerBusFactory(source="mocket", conn_spec=conn_spec)
        self.registry_id = registry_id
        self.device_id = device_id
        self.failure_mode = failure_mode
        self.current_version = "1.0"
        self.lkg_version = "1.0"
        self.status = "quiescent"
        self.model_repo = ModelRepository()
        
        # Proxy bus methods
        self.source = self.bus.source
        self.publish_uufi = self.bus.publish_uufi
        self.subscribe_uufi = self.bus.subscribe_uufi
        self.connect = self.bus.connect
        self.loop_forever = self.bus.loop_forever
        self.start_handshake = self.bus.start_handshake
        
        # Ensure bus uses our registry_id
        self.bus.registry_id = registry_id
        
        self.bus.on_connect = self.on_connect
        self.bus.on_message = self.on_message

    def on_connect(self):
        print(f"[mocket] System/Device connected: {self.device_id}")
        # Subscribe to all traffic to handle device handshakes and states
        self.subscribe_uufi()

    def on_message(self, topic, device_id, sub_type, sub_folder, data):
        # Handle UUFI Handshake (System side response)
        if sub_type == "state" and sub_folder == "udmi":
            self.handle_handshake_state(data)
            return

        # Handle Cloud Model Queries/Updates (UDMIS side)
        if sub_folder == "cloud":
            self.handle_cloud_message(data)
            return

        if device_id != self.device_id:
            return

        # Handle Device Config (Target Device side)
        if sub_type == "config" and sub_folder == "update":
            self.handle_update_config(data)

    def handle_handshake_state(self, data):
        udmi = data.get("udmi", {})
        setup = udmi.get("setup", {})
        transaction_id = setup.get("transaction_id")
        source = data.get("source")
        principal = data.get("principal")
        
        if source == self.source:
            return

        print(f"[mocket] Responding to handshake from {source} ({principal})")
        
        response_payload = {
            "setup": {
                "functions_min": 9,
                "functions_max": 9,
                "udmi_version": "1.5.2",
                "registry_id": self.registry_id
            },
            "reply": {
                "functions_ver": 9,
                "transaction_id": transaction_id,
                "msg_source": source
            }
        }
        # Handshake reply MUST go to /uufi/c/config/udmi (registry-less)
        self.publish_uufi(None, "config", response_payload, "udmi", transaction_id=transaction_id)

    def handle_cloud_message(self, data):
        # Cloud data is wrapped in 'cloud' key
        cloud_data = data.get("cloud", {})
        operation = cloud_data.get("operation")
        target_device = data.get("deviceId")
        transaction_id = data.get("transactionId")
        source = data.get("source")
        
        print(f"[mocket] Handling cloud {operation} for {target_device} from {source}")
        
        if operation == "READ":
            # New structure: {"registries": { "registry_id": { "devices": { "device_id": { ... } } } } }
            if target_device and target_device != "all":
                registry_id = data.get("deviceRegistryId", self.registry_id)
                devices = {target_device: self.model_repo.get_device_subsystems(registry_id, target_device)}
                registries = {registry_id: {"devices": devices}}
            else:
                model = self.model_repo.load_model()
                registries = model.get("registries", {})
            
            payload = {
                "registries": registries
            }
            self.publish_uufi(target_device, "config", payload, "cloud", transaction_id=transaction_id)
            
        elif operation in ["UPDATE", "CREATE"]:
            # Perform update in ModelRepository
            # Structure: {"registries": { "registry_id": { "devices": { "device_id": { "subsystem": { ... } } } } } }
            registries = cloud_data.get("registries", {})
            for reg_id, reg_data in registries.items():
                devices = reg_data.get("devices", {})
                for dev_id, subsystems in devices.items():
                    for subsystem_id, detail in subsystems.items():
                        if operation == "UPDATE":
                            print(f"[mocket] Updating {reg_id}/{dev_id}/{subsystem_id} with {detail}")
                            self.model_repo.update_subsystem(reg_id, dev_id, subsystem_id, **detail)
                        else:
                            print(f"[mocket] Replacing {reg_id}/{dev_id}/{subsystem_id} with {detail}")
                            self.model_repo.save_subsystem(reg_id, dev_id, subsystem_id, detail)
            
            # Confirm change
            payload = { "status": "success", "operation": operation }
            self.publish_uufi(target_device, "config", payload, "cloud", transaction_id=transaction_id)

    def handle_update_config(self, data):
        if self.status == "pending":
            return
        
        # Look inside the 'update' subfolder for the config
        update_config = data.get("update", {})
        target_version = update_config.get("version")
        url = update_config.get("url")
        expected_sha256 = update_config.get("sha256")
        
        if not target_version:
            return

        print(f"[mocket] Device {self.device_id} receiving update config to {target_version}")
        self.status = "pending"
        self.report_state()
        
        threading.Thread(target=self.apply_update, args=(target_version, url, expected_sha256), daemon=True).start()

    def apply_update(self, version, url, expected_sha256):
        time.sleep(2)
        if self.failure_mode:
            print(f"[mocket] Device {self.device_id} failing update (failure mode)")
            self.status = "failure"
        else:
            try:
                success = True
                if url and url.startswith("file://"):
                    path = url[7:]
                    if os.path.exists(path):
                        with open(path, 'rb') as f:
                            actual_sha256 = hashlib.sha256(f.read()).hexdigest()
                        if actual_sha256 != expected_sha256:
                            print(f"[mocket] Device {self.device_id} SHA256 mismatch: {actual_sha256} != {expected_sha256}")
                            success = False
                    else:
                        print(f"[mocket] Device {self.device_id} blob not found: {path}")
                        success = False
                
                if success:
                    print(f"[mocket] Device {self.device_id} update successful to {version}")
                    self.lkg_version = self.current_version
                    self.current_version = version
                    self.status = "success"
                else:
                    self.status = "failure"
            except Exception as e:
                print(f"[mocket] Device {self.device_id} error during update: {e}")
                self.status = "failure"
        
        self.report_state()
        time.sleep(1)
        self.status = "quiescent"
        self.report_state()

    def report_state(self):
        payload = {
            "current_version": self.current_version,
            "lkg_version": self.lkg_version,
            "status": self.status
        }
        self.publish_uufi(self.device_id, "state", payload, "update", registry_id=self.registry_id)

    def heartbeat(self):
        while True:
            if self.bus.handshake_complete:
                self.report_state()
            time.sleep(30)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("conn_spec", help="Connection specification")
    parser.add_argument("registry_id")
    parser.add_argument("device_id")
    parser.add_argument("-f", "--failure", action="store_true", help="Enable failure mode")
    args = parser.parse_args()
    
    device = MocketDevice(args.registry_id, args.device_id, conn_spec=args.conn_spec, failure_mode=args.failure)
    device.connect()
    threading.Thread(target=device.loop_forever, daemon=True).start()
    
    threading.Thread(target=device.heartbeat, daemon=True).start()
    
    while True:
        time.sleep(1)

if __name__ == "__main__":
    main()
