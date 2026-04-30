import json
import datetime
import secrets

def create_message(subfolder, payload, source=None, nonce=None):
    if nonce is None:
        nonce = secrets.token_hex(4) # 8 hex digits
    
    msg = {
        "version": "1.5.2",
        "timestamp": datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
        "nonce": nonce,
        subfolder: payload
    }
    if source:
        msg["source"] = source
    return msg

def create_uufi_message(registry_id, device_id, sub_type, sub_folder, payload, transaction_id=None, source=None):
    if transaction_id is None:
        transaction_id = f"tid-{secrets.token_hex(4)}"
    
    udmi_msg = create_message(sub_folder, payload, source=source)
    envelope = {
        "projectId": "butler-project",
        "deviceRegistryId": registry_id,
        "deviceId": device_id,
        "subFolder": sub_folder,
        "subType": sub_type,
        "transactionId": transaction_id,
        "publishTime": udmi_msg["timestamp"],
        "source": source or "system",
        "payload": udmi_msg
    }
    return envelope

def parse_message(data):
    try:
        return json.loads(data)
    except json.JSONDecodeError:
        return None

def parse_uufi_message(data):
    msg = parse_message(data)
    if msg and "payload" in msg:
        return msg["payload"], msg
    return msg, None
