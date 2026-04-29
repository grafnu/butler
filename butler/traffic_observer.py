import json
from butler.common import ButlerMQTTBase

class TrafficObserver(ButlerMQTTBase):
    def __init__(self, host="localhost", port=1883):
        super().__init__(source="observer", host=host, port=port)

    def on_connect(self):
        print("Traffic Observer connected. Subscribing to devices/#")
        self.subscribe("devices/#")

    def on_message(self, topic, data):
        print(f"\nTopic: {topic}")
        print(json.dumps(data, indent=4))

def main():
    observer = TrafficObserver()
    observer.connect()
    try:
        observer.loop_forever()
    except KeyboardInterrupt:
        print("\nStopping observer...")

if __name__ == "__main__":
    main()
