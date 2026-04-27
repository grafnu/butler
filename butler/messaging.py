import json
import datetime
import secrets

def create_message(source, destination, msg_type, payload):
    return {
        "source": source,
        "destination": destination,
        "type": msg_type,
        "timestamp": datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
        "nonce": secrets.token_hex(4), # 8 hex digits
        "payload": payload
    }

def parse_message(data):
    try:
        return json.loads(data)
    except json.JSONDecodeError:
        return None
