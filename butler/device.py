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
        print(f"[mocket] Device/UDMIS connected: {self.device_id}")
        # Start handshake in a separate thread to allow retries
        threading.Thread(target=self.handshake_loop, daemon=True).start()
        # Subscribe to all config messages for this device
        self.subscribe_uufi()

    def handshake_loop(self):
        while not self.bus.handshake_complete:
            print(f"[mocket] Attempting handshake for {self.device_id}...")
            self.start_handshake(device_id=self.device_id)
            # Wait for 5 seconds for a reply
            for _ in range(50):
                if self.bus.handshake_complete:
                    return
                time.sleep(0.1)

    def on_message(self, topic, device_id, sub_type, sub_folder, data):
        if not self.bus.handshake_complete:
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

    def handle_cloud_message(self, data):
        # Cloud data is wrapped in 'cloud' key
        cloud_data = data.get("cloud", {})
        operation = cloud_data.get("operation")
        target_device = data.get("deviceId")
        transaction_id = data.get("transactionId")
        source = data.get("source")
        
        print(f"[mocket] Handling cloud {operation} for {target_device} from {source}")
        
        if operation == "READ":
            model = self.model_repo.load_model()
            if target_device and target_device != "all":
                raw_devices = {target_device: model.get(target_device)}
            else:
                raw_devices = model
            
            # Nested structure as per 3.2: {"devices": { "device_id": { "subsystem": { ... } } } }
            nested_devices = {}
            for dev_id, dev_data in raw_devices.items():
                if dev_data:
                    subsystem = dev_data.get("subsystem", "main")
                    nested_devices[dev_id] = { subsystem: dev_data }
            
            payload = {
                "devices": nested_devices
            }
            self.publish_uufi(target_device, "config", payload, "cloud", target_principal=source, transaction_id=transaction_id)
            
        elif operation in ["UPDATE", "CREATE"]:
            # Perform update in ModelRepository
            # Structure: {"devices": { "device_id": { "subsystem": { ... } } } }
            devices = cloud_data.get("devices", {})
            for dev_id, subsystems in devices.items():
                for subsystem_id, detail in subsystems.items():
                    print(f"[mocket] Updating {dev_id}/{subsystem_id} with {detail}")
                    self.model_repo.update_device(dev_id, **detail)
            
            # Confirm change
            payload = { "status": "success", "operation": operation }
            self.publish_uufi(target_device, "config", payload, "cloud", target_principal=source, transaction_id=transaction_id)

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
                    self.current_version = version
                    self.status = "success"
                else:
                    self.status = "failure"
            except Exception as e:
                print(f"[mocket] Device {self.device_id} error during update: {e}")
                self.status = "failure"
        
        self.report_state()
        # After success/failure, we also update the model via self.model_repo
        # but the spec says Butler should request the update.
        # However, Mocket IS the model repo manager, so it should probably 
        # just handle its own status reports and sync them if needed.
        # Actually, 4.1 Step 6 says: "Orchestrator sends a model update request to mocket"
        # So mocket just reports status on the bus, butler sees it, and then butler
        # sends a 'cloud' message back to mocket to update the persistent model.
        
        time.sleep(1)
        self.status = "quiescent"
        self.report_state()

    def report_state(self):
        payload = {
            "version": self.current_version,
            "status": self.status
        }
        self.publish_uufi(self.device_id, "state", payload, "update", direction="reflect")

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
