import sys
import time
import json
import re
from butler.transport import parse_conn_spec, MqttTransport, wrap_message

def main():
    if len(sys.argv) < 2:
        print("Usage: bin/verifier conn_spec")
        sys.exit(1)

    conn_spec_str = sys.argv[1]
    conn_spec = parse_conn_spec(conn_spec_str)
    print(f"Conn spec: scheme={conn_spec.scheme}, host={conn_spec.host}, port={conn_spec.port}, principal={conn_spec.principal}, prefix={conn_spec.prefix}")

    transport = MqttTransport(conn_spec)
    device_states = {}

    def publish_verification(msg_str):
        print(f"VERIFICATION: {msg_str}")
        # Results MUST be reported using a UUFI-compliant envelope with the `validation` subFolder.
        try:
            topic = transport.format_topic("events", "validation")
        except ValueError:
            topic = f"{conn_spec.prefix or 'uufi'}/events/validation"

        payload = wrap_message({"validation": {"message": msg_str}})
        transport.publish(topic, payload)

    def validate_schema(payload, is_from_butler=False):
        def check_timestamp(ts):
            if not isinstance(ts, str):
                publish_verification("INVALID SCHEMA: Timestamp is not a string")
                return False
            if is_from_butler and not re.match(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$', ts):
                publish_verification("INVALID SCHEMA: Timestamp not in minimal precision format")
                return False
            return True

        if 'payload' not in payload:
            if 'timestamp' not in payload or 'version' not in payload:
                publish_verification("INVALID SCHEMA: Missing timestamp or version")
                return False
            if not check_timestamp(payload['timestamp']):
                return False
        else:
            if 'timestamp' not in payload['payload'] or 'version' not in payload['payload']:
                publish_verification("INVALID SCHEMA: Missing timestamp or version in payload wrapper")
                return False
            if not check_timestamp(payload['payload']['timestamp']):
                return False
        return True

    def on_message(topic, payload):
        parsed = transport.parse_topic(topic)
        subType = parsed.get('subType')
        subFolder = parsed.get('subFolder')
        device_id = parsed.get('deviceId')

        if subType and subFolder:
            is_from_butler = False
            if (subFolder == 'cloud' and subType in ['model', 'query']) or \
               (subFolder == 'udmi' and subType == 'state') or \
               (subFolder == 'update' and subType == 'config'):
                is_from_butler = True
            validate_schema(payload, is_from_butler=is_from_butler)

        if subType == 'state' and subFolder == 'update' and device_id:
            unwrapped = payload.get('payload', payload)
            update = unwrapped.get('update', {})
            state = update.get('state')

            if state:
                subsystem = "main"
                if device_id not in device_states:
                    device_states[device_id] = {}

                prev_state = device_states[device_id].get(subsystem, 'unknown')

                if state != prev_state:
                    publish_verification(f"State transition for {device_id}: {prev_state} -> {state}")

                    if state == 'success' and prev_state != 'pending':
                        publish_verification(f"INVALID TRANSITION: {device_id} went to success without pending")
                    if state == 'failure' and prev_state != 'pending':
                        publish_verification(f"INVALID TRANSITION: {device_id} went to failure without pending")

                    device_states[device_id][subsystem] = state

    transport.set_on_message(on_message)
    transport.connect()

    if transport.conn_spec.principal:
        transport.handshake()

    transport.subscribe("#")
    print("Verifier started")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        transport.disconnect()

if __name__ == '__main__':
    main()
