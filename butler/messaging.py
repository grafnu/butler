import json
import datetime
import secrets

def create_message(subfolder, payload, nonce=None):
    if nonce is None:
        nonce = secrets.token_hex(4) # 8 hex digits
    
    return {
        "version": "1.5.2",
        "timestamp": datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
        "nonce": nonce,
        subfolder: payload
    }

def parse_message(data):
    try:
        return json.loads(data)
    except json.JSONDecodeError:
        return None
