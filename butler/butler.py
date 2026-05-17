import sys
import time
import argparse
import os
from butler.transport import parse_conn_spec, MqttTransport, wrap_message, unwrap_message, get_timestamp
from butler.blob_repo import BlobRepo
from butler.model_repo import ModelRepo

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

    conn_spec = parse_conn_spec(conn_spec_str)
    print(f"Conn spec: scheme={conn_spec.scheme}, host={conn_spec.host}, port={conn_spec.port}, principal={conn_spec.principal}, prefix={conn_spec.prefix}", flush=True)

    transport = MqttTransport(conn_spec, tag="butler")
    blob_repo = BlobRepo()
    model_repo = ModelRepo()
    device_states = {}
    settling_times = {}

    def fetch_model_state():
        topic = transport.format_topic("query", "cloud")
        msg = wrap_message({"cloud": {"operation": "READ", "registries": {}}}, principal=transport.principal, source=transport.principal)
        transport.publish(topic, msg)

    def on_message(topic, payload):
        parsed = transport.parse_topic(topic)
        subType = parsed.get('subType')
        subFolder = parsed.get('subFolder')
        registry_id = parsed.get('registryId')
        device_id = parsed.get('deviceId')
        unwrapped = unwrap_message(payload)

        if subType == 'state' and subFolder == 'udmi':
            udmi = unwrapped.get('udmi', unwrapped)
            if 'setup' in udmi:
                setup = udmi['setup']
                transaction_id = setup.get('transaction_id')
                if transaction_id:
                    sender_principal = payload.get('principal') or payload.get('source')
                    if sender_principal:
                        reply_topic = transport.format_topic("config", "udmi")
                        reply_payload = wrap_message({
                            "udmi": {
                                "setup": {
                                    "functions_ver": 9,
                                    "deviceRegistryId": os.environ.get("BUTLER_REGISTRY_ID", "reg1")
                                },
                                "reply": {
                                    "transaction_id": transaction_id,
                                    "msg_source": transport.principal
                                }
                            }
                        }, principal=sender_principal, source=transport.principal)
                        transport.publish(reply_topic, reply_payload)
            return

        if subType == 'query' and subFolder == 'cloud':
            # Respond to cloud model query (UUFI 2.2)
            model_data = model_repo.get_model()
            reply_topic = transport.format_topic("config", "cloud")
            sender_principal = payload.get('principal') or payload.get('source')
            transport.publish(reply_topic, wrap_message({"cloud": {"operation": "READ", "registries": model_data.get("registries", {})}}, principal=sender_principal, source=transport.principal))
            return

        if (subType == 'config' or subType == 'model') and subFolder == 'cloud':
            cloud = unwrapped.get('cloud', {})
            # If it's a model update from someone else, ingest it
            if subType == 'model' and cloud.get('operation') == 'UPDATE':
                 registries = cloud.get('registries', {})
                 for reg_id, reg_data in registries.items():
                     devices = reg_data.get('devices', reg_data) # Robustness
                     for dev_id, dev_data in devices.items():
                         for subsystem, data in dev_data.items():
                             if isinstance(data, dict):
                                 model_repo.update_subsystem(reg_id, dev_id, subsystem, data)

            # Refresh local states
            registries = cloud.get('registries', {})
            for reg_id, reg_data in registries.items():
                devices = reg_data.get('devices', reg_data)
                for dev_id, dev_data in devices.items():
                    state_key = (reg_id, dev_id)
                    if state_key not in device_states: device_states[state_key] = {}
                    for subsystem, data in dev_data.items():
                        if not isinstance(data, dict): continue
                        if subsystem not in device_states[state_key]: device_states[state_key][subsystem] = {}
                        state_data = device_states[state_key][subsystem]
                        if 'target_version' in data: state_data['target_version'] = data.get('target_version')
                        if 'current_version' in data: state_data['current_version'] = data.get('current_version')
                        if 'lkg_version' in data: state_data['lkg_version'] = data.get('lkg_version')
                        state_data.setdefault('state', 'quiescent')
            return

        if not registry_id or not device_id: return
        state_key = (registry_id, device_id)
        if state_key not in device_states:
            device_states[state_key] = {}
            fetch_model_state()

        if subType == 'state' and subFolder == 'blobset':
            blobset = unwrapped.get('blobset', {})
            blobs = blobset.get('blobs', blobset)
            # Ensure nesting for flat payloads
            if "status" in blobs or "current_version" in blobs: blobs = {"main": blobs}
            
            for subsystem, sub_update in blobs.items():
                if not isinstance(sub_update, dict) or subsystem in ['version', 'timestamp']: continue
                state = sub_update.get('status') or sub_update.get('state')
                current_version = sub_update.get('current_version')
                if subsystem not in device_states[state_key]: device_states[state_key][subsystem] = {}
                state_data = device_states[state_key][subsystem]
                
                # Persistence (Section 3.2): Update local model file and notify others
                if state in ['success', 'failure', 'quiescent'] and (state != state_data.get('state') or current_version != state_data.get('current_version')):
                    print(f"[butler] Device {registry_id}/{device_id}/{subsystem} terminal state {state} with version {current_version}", flush=True)
                    update_data = {"status": state, "current_version": current_version}
                    model_repo.update_subsystem(registry_id, device_id, subsystem, update_data)
                    
                    topic_model = transport.format_topic("model", "cloud")
                    model_update = {"cloud": {"operation": "UPDATE", "registries": {registry_id: {"devices": {device_id: {subsystem: update_data}}}}}}
                    transport.publish(topic_model, wrap_message(model_update, principal=transport.principal))

                if state != state_data.get('state'): settling_times[state_key + (subsystem,)] = time.time()
                state_data['state'] = state
                if current_version: state_data['current_version'] = current_version

    transport.set_on_message(on_message)
    transport.connect()
    # transport.handshake() # System (Butler) MUST NOT initiate handshake, only respond.
    transport.subscribe(transport.format_topic("state", "udmi"))
    transport.subscribe(transport.format_topic("config", "cloud"))
    transport.subscribe(transport.format_topic("query", "cloud"))
    transport.subscribe(transport.format_topic("model", "cloud"))
    transport.subscribe(transport.format_topic("state", "blobset", registry_id="+", device_id="+"))

    last_model_pub = 0
    try:
        while True:
            now = time.time()
            # Periodically publish current configuration (every 10s)
            if now - last_model_pub > 10:
                model_data = model_repo.get_model()
                registries = model_data.get('registries', {})
                for reg_id, reg_data in registries.items():
                    devices = reg_data.get('devices', reg_data)
                    for dev_id, dev_data in devices.items():
                        state_key = (reg_id, dev_id)
                        if state_key not in device_states: device_states[state_key] = {}
                        for subsystem, data in dev_data.items():
                            if not isinstance(data, dict): continue
                            if subsystem not in device_states[state_key]: device_states[state_key][subsystem] = {}
                            sd = device_states[state_key][subsystem]
                            if 'target_version' in data: sd['target_version'] = data.get('target_version')
                            if 'current_version' in data: sd['current_version'] = data.get('current_version')

                topic_config = transport.format_topic("config", "cloud")
                transport.publish(topic_config, wrap_message({"cloud": {"operation": "READ", "registries": registries}}, principal=transport.principal, source=transport.principal))
                last_model_pub = now

            for (registry_id, device_id), subsystems in list(device_states.items()):
                for subsystem, state_data in subsystems.items():
                    target = state_data.get('target_version')
                    current = state_data.get('current_version') or ""
                    state = state_data.get('state', 'quiescent')
                    if target and target != current:
                        if state == 'pending' and target == state_data.get('pending_version'): continue
                        last_change = settling_times.get((registry_id, device_id, subsystem), 0)
                        if now - last_change < 5.0: continue
                        
                        # Fallback for make/model
                        make = state_data.get('make', 'unknown')
                        model_name = state_data.get('model', 'unknown')
                        
                        blob_info = blob_repo.get_blob_info(make, model_name, subsystem, target)
                        if blob_info:
                            topic_update = transport.format_topic("config", "blobset", registry_id, device_id)
                            msg = wrap_message({"blobset": {"blobs": {subsystem: {"url": blob_info['url'], "sha256": blob_info['hash'], "version": target, "make": make, "model": model_name, "generation": get_timestamp()}}}}, principal=transport.principal)
                            transport.publish(topic_update, msg)
                            state_data['state'] = 'pending'
                            state_data['pending_version'] = target
                            settling_times[(registry_id, device_id, subsystem)] = now
            time.sleep(1)
    except KeyboardInterrupt: pass
    finally: transport.disconnect()

if __name__ == '__main__':
    main()
