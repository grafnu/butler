import sys
import time
import argparse
import uuid
import logging
from butler.transport import parse_conn_spec, MqttTransport, wrap_message, unwrap_message
from butler.blob_repo import BlobRepo

def main():
    parser = argparse.ArgumentParser(description="Butler orchestrator")
    parser.add_argument("conn_spec", help="Connection spec")
    parser.add_argument("-f", "--fail", action="store_true", help="Introduce failure mode")
    args = parser.parse_args()

    conn_spec = parse_conn_spec(args.conn_spec)
    print(f"Conn spec: scheme={conn_spec.scheme}, host={conn_spec.host}, port={conn_spec.port}, principal={conn_spec.principal}, prefix={conn_spec.prefix}")

    transport = MqttTransport(conn_spec, tag="butler")
    blob_repo = BlobRepo()
    device_states = {} # (registry_id, device_id) -> {subsystem: state_data}
    settling_times = {} # (registry_id, device_id, subsystem) -> float

    def fetch_model_state(registry_id, device_id):
        topic = transport.format_topic("query", "cloud", registry_id, device_id)
        msg = wrap_message({"cloud": {"operation": "READ"}}, principal=transport.principal)
        transport.publish(topic, msg)

    def on_message(topic, payload):
        parsed = transport.parse_topic(topic)
        subType = parsed.get('subType')
        subFolder = parsed.get('subFolder')
        registry_id = parsed.get('registryId')
        device_id = parsed.get('deviceId')

        # Discovery: if we see a registry/device we don't know, we can at least track it
        if not registry_id or not device_id:
            # Maybe it's a registry-less message we should care about?
            # For now, we mainly care about registry-associated traffic for device state.
            return

        state_key = (registry_id, device_id)
        if state_key not in device_states:
            device_states[state_key] = {}
            # Try to fetch full model state for this new device
            fetch_model_state(registry_id, device_id)

        unwrapped = unwrap_message(payload)

        if subType == 'config' and subFolder == 'cloud':
            cloud = unwrapped.get('cloud', {})
            registries = cloud.get('registries', {})
            reg_data = registries.get(registry_id, {})
            devices = reg_data.get('devices', {})
            dev_data = devices.get(device_id, {})

            # dev_data is {subsystem: {target_version, current_version, lkg_version}}
            for subsystem, data in dev_data.items():
                if subsystem not in device_states[state_key]:
                    device_states[state_key][subsystem] = {}
                state_data = device_states[state_key][subsystem]
                
                # Only update if fields are present (partial merge)
                if 'target_version' in data:
                    state_data['target_version'] = data.get('target_version')
                if 'current_version' in data:
                    state_data['current_version'] = data.get('current_version')
                if 'lkg_version' in data:
                    state_data['lkg_version'] = data.get('lkg_version')
                
                state_data.setdefault('state', 'quiescent')

        elif subType == 'state' and subFolder == 'update':
            update = unwrapped.get('update', {})
            state = update.get('state')
            current_version = update.get('current_version')
            lkg_version = update.get('lkg_version')

            # Assume 'main' if not specified, though spec says subsystems should be supported
            subsystem = "main" 
            if subsystem not in device_states[state_key]:
                device_states[state_key][subsystem] = {}
            
            state_data = device_states[state_key][subsystem]
            
            # Update internal tracking from device report
            state_data['state'] = state
            if lkg_version:
                state_data['lkg_version'] = lkg_version
            
            # If we don't have a target version yet, we need to fetch it
            if 'target_version' not in state_data:
                fetch_model_state(registry_id, device_id)
                return

            if state == 'success':
                if 'pending_start' in state_data:
                    del state_data['pending_start']
                
                # If current version changed, update model
                if current_version and current_version != state_data.get('current_version'):
                    topic_model = transport.format_topic("model", "cloud", registry_id, device_id)
                    model_update = {
                        "cloud": {
                            "operation": "UPDATE",
                            "registries": {
                                registry_id: {
                                    "devices": {
                                        device_id: {
                                            subsystem: {
                                                "current_version": current_version,
                                                "lkg_version": state_data.get('lkg_version')
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                    transport.publish(topic_model, wrap_message(model_update, principal=transport.principal))
                    state_data['current_version'] = current_version

            elif state == 'failure':
                if 'pending_start' in state_data:
                    del state_data['pending_start']
                
                # Trigger rollback to LKG
                lkg = state_data.get('lkg_version')
                if lkg:
                    topic_model = transport.format_topic("model", "cloud", registry_id, device_id)
                    model_update = {
                        "cloud": {
                            "operation": "UPDATE",
                            "registries": {
                                registry_id: {
                                    "devices": {
                                        device_id: {
                                            subsystem: {
                                                "target_version": lkg
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                    transport.publish(topic_model, wrap_message(model_update, principal=transport.principal))

    transport.set_on_message(on_message)
    transport.connect()

    # UUFI Handshake
    transport.handshake()

    # Dynamic Discovery: subscribe to all uufi traffic to find registries/devices
    # The format is /uufi/p/+/r/+/d/+/c/+/+
    # But also need registry-less for some things
    transport.subscribe("/uufi/p/+/c/config/cloud") # For model queries
    transport.subscribe("/uufi/p/+/r/+/d/+/c/config/cloud")
    transport.subscribe("/uufi/p/+/r/+/d/+/c/state/update")

    try:
        while True:
            now = time.time()
            for (registry_id, device_id), subsystems in list(device_states.items()):
                for subsystem, state_data in subsystems.items():
                    target = state_data.get('target_version')
                    current = state_data.get('current_version')
                    state = state_data.get('state', 'quiescent')

                    if target and current and target != current and state != 'pending':
                        if args.fail:
                            continue

                        # Settling Time: 5s
                        last_change = settling_times.get((registry_id, device_id, subsystem), 0)
                        if now - last_change < 5.0:
                            continue

                        # Re-triggering logic: only if target changed to something else if already pending
                        # (But here we check state != 'pending', so it's fresh or failed/success)
                        
                        blob_info = blob_repo.get_blob_info("default", "default", subsystem, target)
                        if blob_info:
                            topic_update = transport.format_topic("config", "update", registry_id, device_id)
                            msg = wrap_message({
                                "update": {
                                    "url": blob_info['url'],
                                    "sha256": blob_info['hash'],
                                    "version": target
                                }
                            }, principal=transport.principal)
                            transport.publish(topic_update, msg)
                            state_data['pending_start'] = now
                            state_data['state'] = 'pending'
                            settling_times[(registry_id, device_id, subsystem)] = now

                    elif state == 'pending' and 'pending_start' in state_data:
                        # Timeout: 60s
                        if now - state_data['pending_start'] > 60:
                            state_data['state'] = 'failure'
                            del state_data['pending_start']
                            
                            # Log error and potentially trigger rollback (already handled in on_message failure state if device reports it, 
                            # but here we timeout)
                            lkg = state_data.get('lkg_version')
                            if lkg:
                                topic_model = transport.format_topic("model", "cloud", registry_id, device_id)
                                model_update = {
                                    "cloud": {
                                        "operation": "UPDATE",
                                        "registries": {
                                            registry_id: {
                                                "devices": {
                                                    device_id: {
                                                        subsystem: {
                                                            "target_version": lkg
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                                transport.publish(topic_model, wrap_message(model_update, principal=transport.principal))

            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        transport.disconnect()

if __name__ == '__main__':
    main()
