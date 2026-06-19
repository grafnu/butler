import os
import sys
import hashlib
import subprocess

def get_active_branch():
    try:
        # Check if we are in a git repo
        res = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            check=True
        )
        branch = res.stdout.strip()
        if branch:
            return branch
    except Exception:
        pass
    return "unknown"

def get_branch_port(branch):
    h = hashlib.sha256(branch.encode("utf-8")).hexdigest()
    val = int(h, 16)
    return 45000 + (val % 3000)

def parse_conn_spec(conn_spec_str, entity_suffix):
    # If not provided, check env
    if not conn_spec_str:
        conn_spec_str = os.environ.get("BUTLER_CONN_SPEC")
    
    branch = get_active_branch()
    
    if not conn_spec_str:
        # Default to mqtt://<branch>@localhost/
        conn_spec_str = f"mqtt://{branch}@localhost/"

    # Parse scheme://[principal@]host[:port][/prefix]
    # Handle scheme
    scheme = "mqtt"
    rest = conn_spec_str
    if "://" in conn_spec_str:
        scheme, rest = conn_spec_str.split("://", 1)
        
    # Handle prefix (path)
    prefix = None
    if "/" in rest:
        parts = rest.split("/", 1)
        rest = parts[0]
        p_val = parts[1].strip()
        if p_val:
            prefix = p_val
            
    # Handle principal (userinfo)
    implementation_id = branch
    if "@" in rest:
        principal_part, rest = rest.split("@", 1)
        if principal_part:
            implementation_id = principal_part

    # Handle host and port
    host = "localhost"
    port = get_branch_port(branch)
    
    if ":" in rest:
        host, port_str = rest.split(":", 1)
        try:
            port = int(port_str)
        except ValueError:
            pass
    elif rest:
        host = rest

    # Finalize principal with suffix
    principal = f"{implementation_id}.{entity_suffix}"

    return {
        "scheme": scheme,
        "host": host,
        "port": port,
        "principal": principal,
        "prefix": prefix,
        "implementation_id": implementation_id
    }

def print_conn_spec(parsed, file=sys.stderr):
    # Conn spec: scheme={scheme}, host={host}, port={port}, principal={principal}, prefix={prefix}
    prefix_str = parsed["prefix"] if parsed["prefix"] is not None else "None"
    print(
        f"Conn spec: scheme={parsed['scheme']}, host={parsed['host']}, port={parsed['port']}, "
        f"principal={parsed['principal']}, prefix={prefix_str}",
        file=file
    )
