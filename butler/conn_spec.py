import os
import sys
import urllib.parse
import hashlib
import subprocess

def get_branch_name():
    try:
        # Check git branch in working directory
        out = subprocess.check_output(["git", "rev-parse", "--abbrev-ref", "HEAD"], stderr=subprocess.DEVNULL)
        branch = out.decode("utf-8").strip()
        if branch:
            return branch
    except Exception:
        pass
    return "unknown"

def get_branch_port(branch=None):
    if not branch:
        branch = get_branch_name()
    h = hashlib.sha256(branch.encode("utf-8")).hexdigest()
    val = int(h, 16)
    return 45000 + (val % 3000)

def parse_conn_spec(conn_str, entity_suffix):
    branch = get_branch_name()
    branch_port = get_branch_port(branch)
    
    if not conn_str:
        # ASSUMPTION: if conn_str is not provided, we default to local broker on branch-specific port
        conn_str = f"mqtt://{branch}@localhost/"
        
    try:
        parsed = urllib.parse.urlparse(conn_str)
        scheme = parsed.scheme or "mqtt"
        netloc = parsed.netloc or f"{branch}@localhost"
        path = parsed.path or ""
    except Exception:
        scheme = "mqtt"
        netloc = f"{branch}@localhost"
        path = ""
        
    user = None
    host_port = netloc
    if "@" in netloc:
        user, host_port = netloc.split("@", 1)
        
    host = host_port
    port = None
    if ":" in host_port:
        host, port_str = host_port.split(":", 1)
        try:
            port = int(port_str)
        except ValueError:
            pass
            
    if not port:
        port = branch_port
        
    # Every system component MUST resolve and report its principal using: {implementation_id}.{entity_suffix}
    implementation_id = "impl_B"
    principal = f"{implementation_id}.{entity_suffix}"
    
    prefix = path.strip("/")
    if not prefix:
        prefix_val = "None"
        prefix_actual = None
    else:
        prefix_val = prefix
        prefix_actual = prefix
        
    return {
        "scheme": scheme,
        "host": host,
        "port": port,
        "principal": principal,
        "prefix": prefix_actual,
        "prefix_str": prefix_val
    }

def print_conn_spec(parsed_spec):
    prefix_val = parsed_spec["prefix_str"]
    sys.stderr.write(f"Conn spec: scheme={parsed_spec['scheme']}, host={parsed_spec['host']}, port={parsed_spec['port']}, principal={parsed_spec['principal']}, prefix={prefix_val}\n")
    sys.stderr.flush()
