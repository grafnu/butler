import os
import re
import sys
import subprocess
import hashlib

def get_branch_name():
    try:
        res = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True, check=True)
        branch = res.stdout.strip()
        if not branch:
            return "unknown"
        return branch
    except Exception:
        return "unknown"

def get_branch_ports():
    branch = get_branch_name()
    hash_bytes = hashlib.sha256(branch.encode('utf-8')).digest()
    hash_int = int.from_bytes(hash_bytes, byteorder='big')
    mqtt_port = 45000 + (hash_int % 3000)
    etcd_port = 45000 + ((hash_int + 1) % 3000)
    return mqtt_port, etcd_port

def get_branch_port():
    return get_branch_ports()[0]

def parse_conn_spec(args, entity_suffix):
    # Determine the conn_spec argument
    conn_str = None
    
    # 1. Check for --conn_spec flag
    for i, arg in enumerate(args):
        if arg == "--conn_spec" and i + 1 < len(args):
            conn_str = args[i+1]
            # Remove from args
            args.pop(i)
            args.pop(i)
            break
        elif arg.startswith("--conn_spec="):
            conn_str = arg.split("=", 1)[1]
            args.pop(i)
            break
            
    # 2. Check if the BUTLER_CONN_SPEC env is defined (but wait, "The tools should not use BUTLER_CONN_SPEC directly, but rather the caller should explicitly add it to the command line." So we should NOT check BUTLER_CONN_SPEC if we are strict, but let's check it as a fallback if no positional arg is passed).
    # Wait, the spec says: "If the BUTLER_CONN_SPEC env variable is defined, it should use that as the connectivity specification passed in to all tools. The tools should not use BUTLER_CONN_SPEC directly, but rather the caller should explicitly add it to the command line."
    # So the tools themselves just expect it in arguments (via --conn_spec or positional).
    
    # 3. Position-based parsing:
    # "A common pitfall is allowing an optional [conn_spec] to consume the first required positional argument (e.g., site_id). Implementations MUST inspect the first positional argument and, if it does not match a valid connection schema (e.g., mqtt://), treat it as the first functional argument of the tool."
    if not conn_str and len(args) > 1:
        # Check if the first argument (excluding script name) is a valid connection schema
        first_arg = args[1]
        if first_arg.startswith("mqtt://") or first_arg.startswith("pubsub://"):
            conn_str = first_arg
            args.pop(1)
            
    # 4. Fallback default
    if not conn_str:
        branch = get_branch_name()
        conn_str = f"mqtt://{branch}@localhost/"
        
    # Regex pattern: scheme://[user@]host[:port][/path]
    pattern = r"^([a-zA-Z0-9+.-]+)://(?:([^@/]+)@)?([^:/]+)(?::([0-9]+))?(/.*)?$"
    match = re.match(pattern, conn_str)
    if not match:
        raise ValueError(f"Invalid connection spec format: {conn_str}")
        
    scheme = match.group(1)
    user = match.group(2)
    host = match.group(3)
    port_str = match.group(4)
    path = match.group(5)
    
    # "The setup utility should perform a connectivity check to see if the local broker is running. If the broker is not running, the setup utility must invoke the local UDMI tool (specifically impl/udmi/bin/start_local) to start the broker automatically."
    # For pubsub connections, port is None/prohibited, but the log output requires a resolved numeric port. Wait! Let's check what port to output for pubsub. Let's make it 443 or 0. Since MQTT uses 1883 by default:
    if port_str:
        port = int(port_str)
    else:
        if scheme == "mqtt":
            port = get_branch_port()
        else:
            port = 443 # PubSub default TLS port or similar
            
    # Resolve implementation ID
    impl_id = user if user else "unknown"
    principal = f"{impl_id}.{entity_suffix}"
    
    # Parse Prefix
    prefix = None
    if path:
        stripped_path = path.strip("/")
        if stripped_path:
            prefix = stripped_path
            
    # Output to stderr
    # "Conn spec: scheme={scheme}, host={host}, port={port}, principal={principal}, prefix={prefix}"
    # If prefix is None, it should output prefix=None.
    prefix_str = prefix if prefix is not None else "None"
    sys.stderr.write(f"Conn spec: scheme={scheme}, host={host}, port={port}, principal={principal}, prefix={prefix_str}\n")
    sys.stderr.flush()
    
    return {
        "scheme": scheme,
        "host": host,
        "port": port,
        "principal": principal,
        "prefix": prefix,
        "impl_id": impl_id,
        "raw": conn_str
    }
