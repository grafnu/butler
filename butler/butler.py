import sys
import time
import argparse
import uuid
from butler.transport import parse_conn_spec, MqttTransport, wrap_message, unwrap_message
from butler.blob_repo import BlobRepo

def main():
    parser = argparse.ArgumentParser(description="Butler orchestrator")
    parser.add_argument("conn_spec", help="Connection spec")
    parser.add_argument("-f", "--fail", action="store_true", help="Introduce failure mode")
    args = parser.parse_args()

    conn_spec = parse_conn_spec(args.conn_spec)
    print(f"Conn spec: scheme={conn_spec.scheme}, host={conn_spec.host}, port={conn_spec.port}, principal={conn_spec.principal}, prefix={conn_spec.prefix}")

    transport = MqttTransport(conn_spec)
    blob_repo = BlobRepo()
    device_states = {}
    settling_times = {}

    def fetch_model_state(registry_id, device_id):
        topic = transport.format_topic("query", "cloud", registry_id, device_id)
        msg = wrap_message({"cloud": {"operation": "READ"}})
        transport.publish(topic, msg)

    def on_message(topic, payload):
        parsed = transport.parse_topic(topic)
        device_id = parsed.get('deviceId')
        registry_id = parsed.get('registryId')

        if not device_id or not registry_id:
            return

        subType = parsed.get('subType')
        subFolder = parsed.get('subFolder')
        unwrapped = unwrap_message(payload)

        state_key = (registry_id, device_id)

        if subType == 'config' and subFolder == 'cloud':
            cloud = unwrapped.get('cloud', {})
            registries = cloud.get('registries', {})
            reg_data = registries.get(registry_id, {})
            devices = reg_data.get('devices', {})
            dev_data = devices.get(device_id, {})

            if state_key not in device_states:
                device_states[state_key] = {}

            for subsystem, data in dev_data.items():
                if subsystem not in device_states[state_key]:
                    device_states[state_key][subsystem] = {}
                state_data = device_states[state_key][subsystem]
                state_data['target_version'] = data.get('target_version')
                state_data['current_version'] = data.get('current_version')
                state_data['lkg_version'] = data.get('lkg_version')
                state_data['registry_id'] = registry_id

        elif subType == 'state' and subFolder == 'update':
            update = unwrapped.get('update', {})
            state = update.get('state')
            current_version = update.get('current_version')

            if state_key not in device_states:
                device_states[state_key] = {}

            subsystem = "main"
            if subsystem not in device_states[state_key]:
                device_states[state_key][subsystem] = {}

            state_data = device_states[state_key][subsystem]

            if 'target_version' not in state_data:
                fetch_model_state(registry_id, device_id)
                return

            state_data['state'] = state
            state_data['registry_id'] = registry_id

            settling_times[(registry_id, device_id, subsystem)] = time.time()

            if state == 'success':
                if 'pending_start' in state_data:
                    del state_data['pending_start']
                if current_version and current_version != state_data.get('current_version'):
                    topic = transport.format_topic("model", "cloud", registry_id, device_id)
                    msg = wrap_message({
                        "cloud": {
                            "operation": "UPDATE",
                            "detail": {
                                "current_version": current_version
                            }
                        }
                    })
                    transport.publish(topic, msg)
                    state_data['current_version'] = current_version

            elif state == 'failure':
                if 'pending_start' in state_data:
                    del state_data['pending_start']
                topic = transport.format_topic("model", "cloud", registry_id, device_id)
                msg = wrap_message({
                    "cloud": {
                        "operation": "UPDATE",
                        "detail": {
                            "revert_to_lkg": True
                        }
                    }
                })
                transport.publish(topic, msg)

    transport.set_on_message(on_message)
    transport.connect()

    if transport.conn_spec.principal:
        transport.handshake()

    transport.subscribe(transport.format_topic("config", "cloud", "+", "+"))
    transport.subscribe(transport.format_topic("state", "update", "+", "+"))

    try:
        while True:
            now = time.time()
            for (registry_id, device_id), subsystems in device_states.items():
                for subsystem, state_data in subsystems.items():
                    target = state_data.get('target_version')
                    current = state_data.get('current_version')
                    state = state_data.get('state')

                    if target and current and target != current and state == 'quiescent':
                        if args.fail:
                            continue

                        if now - settling_times.get((registry_id, device_id, subsystem), 0) < 5.0:
                            continue

                        blob_info = blob_repo.get_blob_info("default", "default", subsystem, target)
                        if blob_info:
                            topic = transport.format_topic("config", "update", registry_id, device_id)
                            msg = wrap_message({
                                "update": {
                                    "url": blob_info['url'],
                                    "sha256": blob_info['hash'],
                                    "version": target
                                }
                            })
                            transport.publish(topic, msg)
                            state_data['pending_start'] = now
                            settling_times[(registry_id, device_id, subsystem)] = now

                    elif state == 'pending' and 'pending_start' in state_data:
                        if now - state_data['pending_start'] > 60:
                            state_data['state'] = 'failure'
                            del state_data['pending_start']

                            topic = transport.format_topic("model", "cloud", registry_id, device_id)
                            msg = wrap_message({
                                "cloud": {
                                    "operation": "UPDATE",
                                    "detail": {
                                        "revert_to_lkg": True
                                    }
                                }
                            })
                            transport.publish(topic, msg)

            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        transport.disconnect()

if __name__ == '__main__':
    main()
