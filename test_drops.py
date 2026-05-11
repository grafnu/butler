from butler.transport import get_transport
from collections import namedtuple
import json

ConnSpec = namedtuple('ConnSpec', ['protocol', 'host', 'port', 'username', 'principal', 'prefix'])
spec = ConnSpec('mqtt', 'localhost', 1883, None, None, None)
t = get_transport(spec)

class Msg:
    def __init__(self, topic, payload):
        self.topic = topic
        self.payload = payload

# Test cases
cases = [
    # 1. Valid message
    {
        "name": "Valid message",
        "topic": "uufi/r/my-reg/d/my-dev/c/state/update",
        "payload": b'{"payload": {"version": "1.5.2", "timestamp": "2026-05-01T22:32:17Z", "update": {}}}',
        "expected": True
    },
    # 2. Missing payload key
    {
        "name": "Missing payload key",
        "topic": "uufi/r/my-reg/d/my-dev/c/state/update",
        "payload": b'{"version": "1.5.2", "timestamp": "2026-05-01T22:32:17Z", "update": {}}',
        "expected": False
    },
    # 3. Redundant deviceRegistryId
    {
        "name": "Redundant deviceRegistryId",
        "topic": "uufi/r/my-reg/d/my-dev/c/state/update",
        "payload": b'{"payload": {"version": "1.5.2", "timestamp": "2026-05-01T22:32:17Z", "update": {}}, "deviceRegistryId": "my-reg"}',
        "expected": False
    },
    # 4. Redundant deviceId
    {
        "name": "Redundant deviceId",
        "topic": "uufi/r/my-reg/d/my-dev/c/state/update",
        "payload": b'{"payload": {"version": "1.5.2", "timestamp": "2026-05-01T22:32:17Z", "update": {}}, "deviceId": "my-dev"}',
        "expected": False
    }
]

for case in cases:
    called = False
    def cb(*args):
        global called
        called = True
    t.callback = cb
    t.on_message(None, None, Msg(case["topic"], case["payload"]))

    if called == case["expected"]:
        print(f"PASS: {case['name']}")
    else:
        print(f"FAIL: {case['name']} (Expected {case['expected']}, got {called})")

cases.extend([
    # 5. Empty dict
    {
        "name": "Empty dict",
        "topic": "uufi/r/my-reg/d/my-dev/c/state/update",
        "payload": b'{}',
        "expected": False
    },
    # 6. Null payload
    {
        "name": "Null payload",
        "topic": "uufi/r/my-reg/d/my-dev/c/state/update",
        "payload": b'',
        "expected": False
    }
])

print("Running all tests again...")
for case in cases:
    called = False
    def cb(*args):
        global called
        called = True
    t.callback = cb
    t.on_message(None, None, Msg(case["topic"], case["payload"]))

    if called == case["expected"]:
        print(f"PASS: {case['name']}")
    else:
        print(f"FAIL: {case['name']} (Expected {case['expected']}, got {called})")
