import sys
import time
import argparse
from butler.transport import parse_conn_spec, MqttTransport, wrap_message, unwrap_message
from butler.blob_repo import BlobRepo

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
    print(f"Conn spec: scheme={conn_spec.scheme}, host={conn_spec.host}, port={conn_spec.port}, principal={conn_spec.principal}, prefix={conn_spec.prefix}")

    transport = MqttTransport(conn_spec, tag="butler")
    blob_repo = BlobRepo()
    device_states = {}
    settling_times = {}

    def fetch_model_state():
        topic = transport.format_topic("query", "cloud")
        msg = wrap_message({"cloud": {"operation": "READ"}}, principal=transport.principal, source=transport.principal)
        transport.publish(topic, msg)

    def on_message(topic, payload):
        parsed = transport.parse_topic(topic)
        subType = parsed.get('subType')
        subFolder = parsed.get('subFolder')
        registry_id = parsed.get('registryId')
        device_id = parsed.get('deviceId')
        unwrapped = unwrap_message(payload)

        if subType == 'config' and subFolder == 'cloud':
            cloud = unwrapped.get('cloud', {})
            registries = cloud.get('registries', {})
            for reg_id, reg_data in registries.items():
                devices = reg_data.get('devices', {})
                for dev_id, dev_data in devices.items():
                    state_key = (reg_id, dev_id)
                    if state_key not in device_states: device_states[state_key] = {}
                    for subsystem, data in dev_data.items():
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

        if subType == 'state' and subFolder == 'update':
            update = unwrapped.get('update', {})
            if "status" in update or "current_version" in update: update = {"main": update}
            for subsystem, sub_update in update.items():
                if not isinstance(sub_update, dict): continue
                state = sub_update.get('status') or sub_update.get('state')
                current_version = sub_update.get('current_version')
                if subsystem not in device_states[state_key]: device_states[state_key][subsystem] = {}
                state_data = device_states[state_key][subsystem]
                if state == 'success' and current_version and current_version != state_data.get('current_version'):
                    topic_model = transport.format_topic("model", "cloud")
                    model_update = {"cloud": {"operation": "UPDATE", "registries": {registry_id: {"devices": {device_id: {subsystem: {"current_version": current_version, "lkg_version": state_data.get('lkg_version')}}}}}}}
                    transport.publish(topic_model, wrap_message(model_update, principal=transport.principal))
                if state != state_data.get('state'): settling_times[state_key + (subsystem,)] = time.time()
                state_data['state'] = state
                if current_version: state_data['current_version'] = current_version

    transport.set_on_message(on_message)
    transport.connect()
    transport.handshake()
    transport.subscribe("/uufi/c/config/cloud")
    transport.subscribe("/uufi/r/+/d/+/c/state/update")

    last_model_fetch = 0
    try:
        while True:
            now = time.time()
            if now - last_model_fetch > 10:
                fetch_model_state()
                last_model_fetch = now
            for (registry_id, device_id), subsystems in list(device_states.items()):
                for subsystem, state_data in subsystems.items():
                    target = state_data.get('target_version')
                    current = state_data.get('current_version') or ""
                    state = state_data.get('state', 'quiescent')
                    if target and target != current:
                        if state == 'pending' and target == state_data.get('pending_version'): continue
                        last_change = settling_times.get((registry_id, device_id, subsystem), 0)
                        if now - last_change < 5.0: continue
                        blob_info = blob_repo.get_blob_info("default", "default", subsystem, target)
                        if blob_info:
                            topic_update = transport.format_topic("config", "update", registry_id, device_id)
                            msg = wrap_message({"update": {subsystem: {"url": blob_info['url'], "sha256": blob_info['hash'], "version": target}}}, principal=transport.principal)
                            transport.publish(topic_update, msg)
                            state_data['state'] = 'pending'
                            state_data['pending_version'] = target
                            settling_times[(registry_id, device_id, subsystem)] = now
            time.sleep(1)
    except KeyboardInterrupt: pass
    finally: transport.disconnect()

if __name__ == '__main__':
    main()
