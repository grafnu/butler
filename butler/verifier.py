import sys
import time
import json
import re
import argparse
from butler.transport import parse_conn_spec, MqttTransport, wrap_message, unwrap_message

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

    transport = MqttTransport(conn_spec, tag="verifier")
    device_states = {}
    
    def publish_verification(msg_str, registry_id="default", device_id="unknown"):
        print(f"VERIFICATION [{registry_id}/{device_id}]: {msg_str}")
        topic = transport.format_topic("events", "validation", registry_id, device_id)
        payload = wrap_message({"validation": {"message": msg_str}}, principal=transport.principal)
        transport.publish(topic, payload)

    def on_message(topic, payload):
        parsed = transport.parse_topic(topic)
        subType = parsed.get('subType')
        subFolder = parsed.get('subFolder')
        device_id = parsed.get('deviceId')
        registry_id = parsed.get('registryId')
        
        if subType == 'state' and subFolder == 'update' and device_id and registry_id:
            unwrapped = unwrap_message(payload)
            update = unwrapped.get('update', {})
            if "status" in update or "current_version" in update: update = {"main": update}
            for subsystem, sub_update in update.items():
                if not isinstance(sub_update, dict): continue
                state = sub_update.get('status') or sub_update.get('state')
                if state:
                    state_key = (registry_id, device_id)
                    if state_key not in device_states: device_states[state_key] = {}
                    prev_state = device_states[state_key].get(subsystem, 'quiescent')
                    if state != prev_state:
                        publish_verification(f"State transition: {prev_state} -> {state}", registry_id, device_id)
                        device_states[state_key][subsystem] = state

    transport.set_on_message(on_message)
    transport.connect()
    transport.handshake()
    transport.subscribe("/uufi/#")
    print("Verifier started and active")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt: pass
    finally: transport.disconnect()

if __name__ == '__main__':
    main()
