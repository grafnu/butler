import sys
import time
import argparse
import logging
from butler.transport import parse_conn_spec, MqttTransport, wrap_message, unwrap_message
from butler.model_repo import ModelRepo
import urllib.request
import hashlib

def main():
    parser = argparse.ArgumentParser(description="Mocket device simulator")
    parser.add_argument("conn_spec", help="Connection spec")
    parser.add_argument("registry_id", help="Registry ID")
    parser.add_argument("device_id", help="Device ID")
    parser.add_argument("-f", "--fail", action="store_true", help="Introduce failure mode")
    args = parser.parse_args()

    conn_spec = parse_conn_spec(args.conn_spec)
    print(f"Conn spec: scheme={conn_spec.scheme}, host={conn_spec.host}, port={conn_spec.port}, principal={conn_spec.principal}, prefix={conn_spec.prefix}")

    transport = MqttTransport(conn_spec, tag="mocket")
    model_repo = ModelRepo()
    registry_id = args.registry_id
    device_id = args.device_id

    state = "quiescent"
    current_version = None
    lkg_version = None
    subsystem = "main"

    def handle_handshake(topic, payload):
        # If we see a state/udmi, we should respond with config/udmi to confirm the handshake
        try:
            unwrapped = unwrap_message(payload, topic_parts=transport.parse_topic(topic))
        except ValueError as e:
            logging.error(f"Mocket drop invalid handshake: {e}")
            return

        if 'udmi' in unwrapped and 'setup' in unwrapped['udmi']:
            setup = unwrapped['udmi']['setup']
            transaction_id = setup.get('transaction_id')
            if transaction_id:
                # Respond to the principal that sent the state
                sender_principal = payload.get('principal') or payload.get('source')
                if sender_principal:
                    reply_topic = transport.format_topic("config", "udmi")
                    reply_payload = wrap_message({
                        "udmi": {
                            "setup": {
                                "functions_min": 9,
                                "functions_max": 9,
                                "udmi_version": "1.5.2"
                            },
                            "reply": {
                                "functions_ver": 9,
                                "transaction_id": transaction_id,
                                "msg_source": transport.principal
                            }
                        }
                    }, transactionId=transaction_id, principal=sender_principal, source=transport.principal)
                    transport.publish(reply_topic, reply_payload)

    def handle_cloud_query(topic, payload):
        try:
            unwrapped = unwrap_message(payload, topic_parts=transport.parse_topic(topic))
        except ValueError as e:
            logging.error(f"Mocket drop invalid cloud query: {e}")
            return

        cloud = unwrapped.get('cloud', {})
        if cloud.get('operation') == 'READ':
            model = model_repo.get_model()
            reg = model.get('registries', {}).get(registry_id, {})
            devices = reg.get('devices', {})
            dev_data = devices.get(device_id, {})
            # dev_data is {subsystem: {target_version, ...}}

            # Format according to spec: registries -> reg_id -> devices -> dev_id -> subsystem -> data
            reply_topic = transport.format_topic("config", "cloud") # Registry-less

            sender_principal = payload.get('principal') or payload.get('source')

            # Spec says "When replying to a model query, the cloud payload MUST follow the nested structure..."
            reply_cloud = {
                "registries": {
                    registry_id: {
                        "devices": {
                            device_id: dev_data
                        }
                    }
                }
            }

            reply_payload = wrap_message({"cloud": reply_cloud}, principal=sender_principal, source=transport.principal)
            transport.publish(reply_topic, reply_payload)

    def handle_cloud_model(topic, payload):
        try:
            unwrapped = unwrap_message(payload, topic_parts=transport.parse_topic(topic))
        except ValueError as e:
            logging.error(f"Mocket drop invalid cloud model: {e}")
            return

        cloud = unwrapped.get('cloud', {})
        if cloud.get('operation') == 'UPDATE':
            registries = cloud.get('registries', {})
            reg_data = registries.get(registry_id, {})
            devices = reg_data.get('devices', {})
            dev_data = devices.get(device_id, {})

            for sub_name, data in dev_data.items():
                model_repo.update_subsystem(registry_id, device_id, sub_name, data)
                # lkg_version update if needed? Usually butler does this.

    def verify_blob(url, expected_hash):
        try:
            if url.startswith("file://"):
                path = url[7:]
                with open(path, "rb") as f:
                    data = f.read()
            else:
                response = urllib.request.urlopen(url)
                data = response.read()

            actual_hash = hashlib.sha256(data).hexdigest()
            return actual_hash == expected_hash
        except Exception as e:
            print(f"Failed to verify blob: {e}")
            return False

    def handle_update(topic, payload, parsed_topic):
        nonlocal state, current_version, subsystem
        try:
            unwrapped = unwrap_message(payload, topic_parts=parsed_topic)
        except ValueError as e:
            logging.error(f"Mocket drop invalid update: {e}")
            return

        update_wrap = unwrapped.get('update', {})

        for sub_name, sub_update in update_wrap.items():
            if not isinstance(sub_update, dict):
                continue

            subsystem = sub_name
            if 'url' in sub_update and 'sha256' in sub_update:
                state = "pending"
                publish_status()

                if args.fail:
                    time.sleep(1)
                    state = "failure"
                    publish_status()
                    return

                time.sleep(2)
                if verify_blob(sub_update['url'], sub_update['sha256']):
                    state = "success"
                    current_version = sub_update.get('version')
                else:
                    state = "failure"

                publish_status()

    def on_message(topic, payload):
        parsed = transport.parse_topic(topic)
        subType = parsed.get('subType')
        subFolder = parsed.get('subFolder')

        # Handshake handling
        if subType == 'state' and subFolder == 'udmi':
            handle_handshake(topic, payload)
            return

        if subType == 'query' and subFolder == 'cloud':
            handle_cloud_query(topic, payload)
        elif subType == 'model' and subFolder == 'cloud':
            handle_cloud_model(topic, payload)
        elif parsed.get('deviceId') == device_id and parsed.get('registryId') == registry_id:
            if subType == 'config' and subFolder == 'update':
                handle_update(topic, payload, parsed)

    def publish_status():
        topic = transport.format_topic("state", "update", registry_id, device_id)
        msg = wrap_message({
            "update": {
                subsystem: {
                    "status": state,
                    "current_version": current_version,
                    "lkg_version": lkg_version
                }
            }
        }, principal=transport.principal, source=transport.principal)
        transport.publish(topic, msg)

    def publish_model():
        model = model_repo.get_model()
        topic = transport.format_topic("config", "cloud") # Registry-less

        # Format according to spec: registries -> reg_id -> devices -> dev_id -> subsystem -> data
        # We broadcast the whole model for this mocket's registry/device at least
        # But usually mocket handles one device. The model_repo might have more.

        reply_payload = wrap_message({"cloud": model}, principal=transport.principal, source=transport.principal)
        transport.publish(topic, reply_payload)

    transport.set_on_message(on_message)
    transport.connect()

    # Mocket might need to handshake if it has a higher-level system, but here it's the system
    # We'll just subscribe to handshakes
    transport.subscribe("/uufi/c/state/udmi")

    transport.subscribe(transport.format_topic("query", "cloud"))
    transport.subscribe(transport.format_topic("model", "cloud"))
    transport.subscribe(transport.format_topic("config", "update", registry_id, device_id))

    try:
        last_pub = 0
        while True:
            now = time.time()
            if now - last_pub > 5:
                # Sync current/lkg from model repo if not set
                model = model_repo.get_model()
                reg = model.get('registries', {}).get(registry_id, {})
                dev = reg.get('devices', {}).get(device_id, {})
                sub_data = dev.get(subsystem, {})

                if current_version is None:
                    current_version = sub_data.get('current_version')
                if lkg_version is None:
                    lkg_version = sub_data.get('lkg_version')

                publish_status()
                publish_model()
                last_pub = now
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        transport.disconnect()

if __name__ == '__main__':
    main()
