import json
import datetime
import secrets
import os

def create_payload(sub_folder, payload_data):
    """Creates the inner UDMI payload."""
    now = datetime.datetime.now(datetime.timezone.utc)
    return {
        "version": "1.5.2",
        "timestamp": now.strftime('%Y-%m-%dT%H:%M:%SZ'),
        sub_folder: payload_data
    }

def create_envelope(transaction_id=None, nonce=None, source=None, project_id=None, 
                    registry_id=None, device_id=None, sub_type=None, sub_folder=None,
                    principal=None):
    """Creates the UUFI envelope metadata."""
    if transaction_id is None:
        transaction_id = f"tid-{secrets.token_hex(4)}"
    if nonce is None:
        nonce = secrets.token_hex(4)
    
    now = datetime.datetime.now(datetime.timezone.utc)
    envelope = {
        "transactionId": transaction_id,
        "nonce": nonce,
        "publishTime": now.strftime('%Y-%m-%dT%H:%M:%SZ'),
    }
    
    if source: envelope["source"] = source
    if project_id: envelope["projectId"] = project_id
    if principal: envelope["principal"] = principal
    if registry_id: envelope["deviceRegistryId"] = registry_id
    if device_id: envelope["deviceId"] = device_id
    if sub_type: envelope["subType"] = sub_type
    if sub_folder: envelope["subFolder"] = sub_folder
        
    return envelope

def parse_message(data):
    try:
        if isinstance(data, bytes):
            data = data.decode('utf-8')
        return json.loads(data)
    except Exception:
        return None
