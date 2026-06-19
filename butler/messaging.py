import json
import datetime
import secrets
import os

def create_payload(sub_folder, payload_data, transaction_id=None):
    """Creates the inner UDMI payload."""
    now = datetime.datetime.now(datetime.timezone.utc)
    res = {
        "version": "1.5.2",
        "timestamp": now.strftime('%Y-%m-%dT%H:%M:%SZ'), # UUFI 8.2: No fractional seconds
    }
    
    if sub_folder == "udmi":
        # UUFI 8.1 / 3: UDMI Subfolder Nesting
        res[sub_folder] = payload_data
        if transaction_id:
            # UUFI 7.1: transaction_id (snake_case) in payload
            if "setup" in res[sub_folder]:
                res[sub_folder]["setup"]["transaction_id"] = transaction_id
            if "reply" in res[sub_folder]:
                res[sub_folder]["reply"]["transaction_id"] = transaction_id
    elif sub_folder:
        res[sub_folder] = payload_data
    else:
        res.update(payload_data)
        if transaction_id and "transaction_id" not in res:
            res["transaction_id"] = transaction_id
            
    return res

def create_envelope(**kwargs):
    """Creates the UUFI envelope metadata."""
    transaction_id = kwargs.get("transactionId") or kwargs.get("transaction_id") or secrets.token_hex(4)
    nonce = kwargs.get("nonce") or secrets.token_hex(4)
    project_id = kwargs.get("projectId") or kwargs.get("project_id") or "vibrant"
    
    now = datetime.datetime.now(datetime.timezone.utc)
    envelope = {
        "transactionId": transaction_id,
        "nonce": nonce,
        "publishTime": now.strftime('%Y-%m-%dT%H:%M:%SZ'),
        "projectId": project_id
    }
    
    mapping = {
        "source": "source",
        "principal": "principal",
        "registry_id": "deviceRegistryId",
        "deviceRegistryId": "deviceRegistryId",
        "device_id": "deviceId",
        "deviceId": "deviceId",
        "sub_type": "subType",
        "subType": "subType",
        "sub_folder": "subFolder",
        "subFolder": "subFolder"
    }
    
    for k, v in kwargs.items():
        if k in mapping and v:
            envelope[mapping[k]] = v
        elif v:
            envelope[k] = v
        
    return envelope

def parse_message(data):
    try:
        if isinstance(data, bytes):
            data = data.decode('utf-8')
        return json.loads(data)
    except Exception:
        return None
