import json
import os
import argparse
import sys
from butler.common import ButlerBusFactory, get_default_conn_spec

class ButlerTrafficObserver:
    def __init__(self, conn_spec=None):
        conn_spec = conn_spec or get_default_conn_spec()
        self.bus = ButlerBusFactory(source="observe", conn_spec=conn_spec)
        self.bus.on_connect = self.on_connect
        self.bus.on_message = self.on_message
        self.bus.on_raw_message = self.on_raw_message

    def on_connect(self):
        print(f"[observe] Observer connected, tapping into message stream...")
        self.bus.subscribe_uufi()

    def on_message(self, topic, device_id, sub_type, sub_folder, data):
        # Protocol Decoupling & Graceful Degradation: data is already parsed JSON
        # Output on one line, including complete message payload
        print(f"{topic}: {json.dumps(data)}")
        sys.stdout.flush()

    def on_raw_message(self, topic, data):
        # Raw Support: If a message payload is not valid JSON, it MUST be displayed in its raw string format.
        print(f"{topic}: {data}")
        sys.stdout.flush()

    def run(self):
        self.bus.connect()
        self.bus.loop_forever()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("conn_spec", help="Connection specification")
    args = parser.parse_args()

    observer = ButlerTrafficObserver(conn_spec=args.conn_spec)
    observer.run()

if __name__ == "__main__":
    main()
