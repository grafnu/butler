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
    parser.add_argument("device_id", help="Device ID")
    parser.add_argument("-f", "--fail", action="store_true", help="Introduce failure mode")
    args = parser.parse_args()

    conn_spec = parse_conn_spec(args.conn_spec)
    print(f"Conn spec: scheme={conn_spec.scheme}, host={conn_spec.host}, port={conn_spec.port}, principal={conn_spec.principal}, prefix={conn_spec.prefix}")

    transport = MqttTransport(conn_spec)
    model_repo = ModelRepo()
    registry_id = "default_registry"

    state = "quiescent"
    current_version = None
    subsystem = "default"

    def handle_cloud_query(topic, payload):
        unwrapped = unwrap_message(payload)
        cloud = unwrapped.get('cloud', {})
        if cloud.get('operation') == 'READ':
            model = model_repo.get_model()
            device = model.get('devices', {}).get(args.device_id, {})
            sub = device.get('subsystems', {}).get(subsystem, {})

            reply_topic = transport.format_topic("config", "cloud", registry_id, args.device_id)
            reply_payload = wrap_message({
                "cloud": {
                    "devices": {
                        args.device_id: {
                            subsystem: {
                                "target_version": sub.get('target_version'),
                                "current_version": sub.get('current_version'),
                                "lkg_version": sub.get('lkg_version')
                            }
                        }
                    }
                }
            })
            transport.publish(reply_topic, reply_payload)

    def handle_cloud_model(topic, payload):
        unwrapped = unwrap_message(payload)
        cloud = unwrapped.get('cloud', {})
        if cloud.get('operation') == 'UPDATE':
            detail = cloud.get('detail', {})
            if 'current_version' in detail:
                model_repo.update_current_version(args.device_id, subsystem, detail['current_version'])
            if 'revert_to_lkg' in detail and detail['revert_to_lkg']:
                model_repo.revert_to_lkg(args.device_id, subsystem)

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

    def handle_update(topic, payload):
        nonlocal state, current_version
        unwrapped = unwrap_message(payload)
        update = unwrapped.get('update', {})

        if 'url' in update and 'sha256' in update:
            state = "pending"
            publish_status()

            if args.fail:
                time.sleep(1)
                state = "failure"
                publish_status()
                return

            time.sleep(2)
            if verify_blob(update['url'], update['sha256']):
                state = "success"
                current_version = update.get('version')
            else:
                state = "failure"

            publish_status()

    def on_message(topic, payload):
        parsed = transport.parse_topic(topic)
        if parsed.get('deviceId') == args.device_id:
            subType = parsed.get('subType')
            subFolder = parsed.get('subFolder')

            if subType == 'query' and subFolder == 'cloud':
                handle_cloud_query(topic, payload)
            elif subType == 'model' and subFolder == 'cloud':
                handle_cloud_model(topic, payload)
            elif subType == 'config' and subFolder == 'update':
                handle_update(topic, payload)

    def publish_status():
        topic = transport.format_topic("state", "update", registry_id, args.device_id)
        msg = wrap_message({
            "update": {
                "state": state,
                "current_version": current_version
            }
        })
        transport.publish(topic, msg)

    transport.set_on_message(on_message)
    transport.connect()

    if transport.conn_spec.principal:
        transport.handshake()

    transport.subscribe(transport.format_topic("query", "cloud", registry_id, args.device_id))
    transport.subscribe(transport.format_topic("model", "cloud", registry_id, args.device_id))
    transport.subscribe(transport.format_topic("config", "update", registry_id, args.device_id))

    try:
        last_pub = 0
        while True:
            now = time.time()
            if now - last_pub > 5:
                if current_version is None:
                    model = model_repo.get_model()
                    device = model.get('devices', {}).get(args.device_id, {})
                    sub = device.get('subsystems', {}).get(subsystem, {})
                    current_version = sub.get('current_version')

                publish_status()
                last_pub = now
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        transport.disconnect()

if __name__ == '__main__':
    main()
