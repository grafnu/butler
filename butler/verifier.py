import sys
import time
import json
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
        topic_base = conn_spec.prefix or "butler"
        topic = f"{topic_base}/verify"
        transport.client.publish(topic, json.dumps({"message": msg_str}))

    def validate_schema(payload):
        if 'payload' not in payload:
            if 'timestamp' not in payload or 'version' not in payload:
                publish_verification("INVALID SCHEMA: Missing timestamp or version")
                return False
        else:
            if 'timestamp' not in payload['payload'] or 'version' not in payload['payload']:
                publish_verification("INVALID SCHEMA: Missing timestamp or version in payload wrapper")
                return False
        return True

    def on_message(topic, payload):
        parsed = transport.parse_topic(topic)
        subType = parsed.get('subType')
        subFolder = parsed.get('subFolder')
        device_id = parsed.get('deviceId')

        if subType and subFolder:
            validate_schema(payload)

        if subType == 'state' and subFolder == 'update' and device_id:
            unwrapped = payload.get('payload', payload)
            update = unwrapped.get('update', {})
            state = update.get('state')

            if state:
                subsystem = "default"
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
