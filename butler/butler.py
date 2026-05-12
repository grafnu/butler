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
    device_states = {} # (registry_id, device_id) -> state_data
    settling_times = {} # (registry_id, device_id) -> float

    def fetch_model_state():
        topic = transport.format_topic("query", "cloud") # Registry-less
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
                    for subsystem, sub_data in dev_data.items():
                        state_key = (reg_id, dev_id, subsystem)
                        if state_key not in device_states:
                            device_states[state_key] = {}
                        
                        state_data = device_states[state_key]

                        if 'target_version' in sub_data:
                            state_data['target_version'] = sub_data.get('target_version')
                        if 'current_version' in sub_data:
                            state_data['current_version'] = sub_data.get('current_version')
                        if 'lkg_version' in sub_data:
                            state_data['lkg_version'] = sub_data.get('lkg_version')

                            state_data.setdefault('state', 'quiescent')
            return

        # For other messages, we still need registry_id and device_id from topic
        if not registry_id or not device_id:
            return

        state_key = (registry_id, device_id, "main")
        if state_key not in device_states:
            device_states[state_key] = {}
            # Try to fetch full model state
            fetch_model_state()

        if subType == 'state' and subFolder == 'update':
            update = unwrapped.get('update', {})

            state = update.get('status') or update.get('state') # Backward compatibility
            current_version = update.get('current_version')
            lkg_version = update.get('lkg_version')

            if state_key not in device_states:
                device_states[state_key] = {}

            state_data = device_states[state_key]

            # If current version changed, update model
            if state == 'success' and current_version and current_version != state_data.get('current_version'):
                topic_model = transport.format_topic("model", "cloud")
                model_update = {
                    "cloud": {
                        "operation": "UPDATE",
                        "registries": {
                            registry_id: {
                                "devices": {
                                    device_id: {
                                        "main": {
                                            "current_version": current_version,
                                            "lkg_version": state_data.get('lkg_version')
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
                transport.publish(topic_model, wrap_message(model_update, principal=transport.principal, source=transport.principal))

            # Update internal tracking from device report
            if state != state_data.get('state'):
                settling_times[state_key] = time.time()

            state_data['state'] = state
            if current_version:
                state_data['current_version'] = current_version
            if lkg_version:
                state_data['lkg_version'] = lkg_version
                
            if state == 'failure' and 'pending_version' in state_data:
                # Rollback logic: if a failure happened, we should revert the model
                lkg = state_data.get('lkg_version')
                if lkg:
                    topic_model = transport.format_topic("model", "cloud")
                    model_update = {
                        "cloud": {
                            "operation": "UPDATE",
                            "registries": {
                                registry_id: {
                                    "devices": {
                                        device_id: {
                                            "main": {
                                                "target_version": lkg
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                    transport.publish(topic_model, wrap_message(model_update, principal=transport.principal, source=transport.principal))

                # Update internal tracking from device report
                if state != state_data.get('state'):
                    settling_times[state_key] = time.time()

                state_data['state'] = state
                if current_version:
                    state_data['current_version'] = current_version
                if lkg_version:
                    state_data['lkg_version'] = lkg_version

                # If we don't have a target version yet, we need to fetch it
                if 'target_version' not in state_data:
                    fetch_model_state()
                    return

                if state == 'success':
                    if 'pending_start' in state_data:
                        del state_data['pending_start']

                elif state == 'failure':
                    if 'pending_start' in state_data:
                        del state_data['pending_start']

                    # Trigger rollback to LKG
                    lkg = state_data.get('lkg_version')
                    if lkg:
                        topic_model = transport.format_topic("model", "cloud")
                        model_update = {
                            "cloud": {
                                "operation": "UPDATE",
                                "registries": {
                                    registry_id: {
                                        "devices": {
                                            device_id: {
                                                "main": {
                                                    "target_version": lkg
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                        transport.publish(topic_model, wrap_message(model_update, principal=transport.principal, source=transport.principal))

    transport.set_on_message(on_message)
    transport.connect()

    # UUFI Handshake
    transport.handshake()

    # Dynamic Discovery: subscribe to all uufi traffic to find registries/devices
    transport.subscribe("/uufi/c/config/cloud") # For model queries
    transport.subscribe("/uufi/r/+/d/+/c/state/update")

    last_model_fetch = 0

    try:
        while True:
            now = time.time()

            # Periodically poll model state (every 10s)
            if now - last_model_fetch > 10:
                fetch_model_state()
                last_model_fetch = now

            for (registry_id, device_id, subsystem), state_data in list(device_states.items()):
                target = state_data.get('target_version')
                current = state_data.get('current_version') or "" # Initial provisioning (null == "")
                state = state_data.get('state', 'quiescent')
                pending_version = state_data.get('pending_version')

                # Trigger if target != current AND (not pending OR target changed while pending)
                if target and target != current:
                    if args.fail:
                        continue

                    # Re-triggering logic: only if target changed to something else if already pending
                    if state == 'pending' and target == pending_version:
                        continue

                    # Settling Time: 5s
                    last_change = settling_times.get((registry_id, device_id, subsystem), 0)
                    if now - last_change < 5.0:
                        continue

                    blob_info = blob_repo.get_blob_info("default", "default", "main", target)
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
                        state_data['pending_version'] = target
                        settling_times[(registry_id, device_id, subsystem)] = now

                elif state == 'pending' and 'pending_start' in state_data:
                    # Timeout: 60s
                    if now - state_data['pending_start'] > 60:
                        state_data['state'] = 'failure'
                        del state_data['pending_start']
                        if 'pending_version' in state_data:
                            del state_data['pending_version']

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
