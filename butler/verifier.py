import sys
import time
import json
import re
from butler.transport import parse_conn_spec, MqttTransport, wrap_message, unwrap_message

def main():
    if len(sys.argv) < 2:
        print("Usage: bin/verifier conn_spec")
        sys.exit(1)

    conn_spec_str = sys.argv[1]
    conn_spec = parse_conn_spec(conn_spec_str)
    print(f"Conn spec: scheme={conn_spec.scheme}, host={conn_spec.host}, port={conn_spec.port}, principal={conn_spec.principal}, prefix={conn_spec.prefix}")

    transport = MqttTransport(conn_spec, tag="verifier")
    device_states = {}
    active_principals = set()

    def publish_verification(msg_str, registry_id="default", device_id="unknown"):
        print(f"VERIFICATION [{registry_id}/{device_id}]: {msg_str}")
        # Results MUST be reported using a UUFI-compliant envelope with the `validation` subFolder.
        # Topic: /uufi/r/{registry_id}/d/{device_id}/validation
        topic = transport.format_topic("events", "validation", registry_id, device_id)

        payload = wrap_message({"validation": {"message": msg_str}}, principal=transport.principal)
        transport.publish(topic, payload)

    def validate_schema(topic, payload, is_from_butler=False):
        parsed = transport.parse_topic(topic)
        registry_id = parsed.get('registryId', 'default')
        device_id = parsed.get('deviceId', 'unknown')

        def check_timestamp(ts):
            if not isinstance(ts, str):
                publish_verification("INVALID SCHEMA: Timestamp is not a string", registry_id, device_id)
                return False
            # RFC 3339 minimal precision: YYYY-MM-DDTHH:MM:SSZ
            if is_from_butler and not re.match(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$', ts):
                publish_verification(f"INVALID SCHEMA: Butler timestamp '{ts}' not in minimal precision format", registry_id, device_id)
                return False
            return True

        unwrapped = unwrap_message(payload)
        
        # Check mandatory fields in inner payload
        inner_payload = payload.get('payload', {})
        if 'timestamp' not in inner_payload or 'version' not in inner_payload:
            publish_verification("INVALID SCHEMA: Missing mandatory inner payload fields (timestamp/version)", registry_id, device_id)
            return False
        
        if not check_timestamp(inner_payload['timestamp']):
            return False
            
        return True

    def on_message(topic, payload):
        parsed = transport.parse_topic(topic)
        subType = parsed.get('subType')
        subFolder = parsed.get('subFolder')
        device_id = parsed.get('deviceId')
        registry_id = parsed.get('registryId')
        
        # In new spec, principal is in the envelope
        principal = payload.get('principal') or payload.get('source')

        # Handshake awareness
        if subType == 'config' and subFolder == 'udmi':
            unwrapped = unwrap_message(payload)
            if 'udmi' in unwrapped and 'reply' in unwrapped['udmi']:
                if principal:
                    active_principals.add(principal)
                    print(f"Verifier observed activation of principal: {principal}")

        if subType and subFolder:
            is_from_butler = False
            # Identify butler-originated messages
            # For cloud queries/models, principal might be the best way to identify butler
            # but we can also use topic patterns.
            if (subFolder == 'cloud' and subType in ['model', 'query']) or \
               (subFolder == 'update' and subType == 'config'):
                is_from_butler = True
            
            validate_schema(topic, payload, is_from_butler=is_from_butler)

        if subType == 'state' and subFolder == 'update' and device_id and registry_id:
            unwrapped = unwrap_message(payload)
            update = unwrapped.get('update', {})
            state = update.get('state')

            if state:
                subsystem = "main"
                state_key = (registry_id, device_id)
                if state_key not in device_states:
                    device_states[state_key] = {}

                prev_state = device_states[state_key].get(subsystem, 'quiescent')

                if state != prev_state:
                    publish_verification(f"State transition: {prev_state} -> {state}", registry_id, device_id)

                    if state == 'success' and prev_state != 'pending':
                        publish_verification(f"INVALID TRANSITION: Went to success without pending", registry_id, device_id)
                    if state == 'failure' and prev_state != 'pending':
                        publish_verification(f"INVALID TRANSITION: Went to failure without pending", registry_id, device_id)

                    device_states[state_key][subsystem] = state

    transport.set_on_message(on_message)
    transport.connect()

    # Active Handshake
    transport.handshake()

    # Subscribe to everything to monitor
    transport.subscribe("/uufi/#")
    print("Verifier started and active")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        transport.disconnect()

if __name__ == '__main__':
    main()
