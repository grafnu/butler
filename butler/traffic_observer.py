import paho.mqtt.client as mqtt
import sys
import argparse
from butler.conn_spec import parse_conn_spec

def on_connect(client, userdata, flags, rc, conn_spec):
    if rc == 0:
        topic = "/uufi/#"
        if conn_spec.prefix:
            topic = f"/uufi/{conn_spec.prefix}/#"
        client.subscribe(topic)
    else:
        print(f"Observer failed to connect: {rc}", flush=True)
        sys.exit(1)

def on_message(client, userdata, msg):
    payload = msg.payload.decode('utf-8', errors='replace')
    print(f"{msg.topic}: {payload}", flush=True)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("conn_spec", help="Connection spec URL")
    args = parser.parse_args()

    conn_spec = parse_conn_spec(args.conn_spec)
    
    host = conn_spec.host
    port = conn_spec.port or 1883
    print(f"Observer connecting to MQTT broker at {host}:{port}", flush=True)
    
    client = mqtt.Client(userdata=conn_spec)
    client.on_connect = lambda c, u, f, r: on_connect(c, u, f, r, conn_spec)
    client.on_message = on_message

    if conn_spec.username:
        client.username_pw_set(conn_spec.username)

    try:
        client.connect(host, port, 60)
        client.loop_forever()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr, flush=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
