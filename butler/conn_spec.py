from urllib.parse import urlparse
import re
import os
import subprocess
import secrets

# Verify impl/udmi exists relative to workspace root on startup as required by spec/butler.md Section 10
workspace_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
udmi_dir = os.path.join(workspace_root, 'impl', 'udmi')
if not os.path.exists(udmi_dir):
    raise FileNotFoundError(f"Hard Fail: Cloned UDMI directory not found at {udmi_dir}")

class ConnSpec:
    def __init__(self, conn_str, differentiator=None, is_passive=False):
        self.conn_str = conn_str
        self.is_passive = is_passive
        parsed = urlparse(conn_str)
        self.protocol = parsed.scheme
        
        # Unique Source ID (UUFI Section 10.1: unique client session identifier)
        self.source_id = f"{differentiator or 'uufi'}-{secrets.token_hex(4)}"
        
        # user@host:port
        username = parsed.username
        self.password = parsed.password
        self.host = parsed.hostname or "localhost"
        
        if self.protocol == "mqtt":
            if parsed.port:
                self.port = parsed.port
            else:
                import hashlib
                import socket
                branch_name = get_branch()
                h = hashlib.sha256(branch_name.encode('utf-8'))
                hash_integer = int(h.hexdigest(), 16)
                initial_port = 45000 + (hash_integer % 3000)
                
                # Dynamic Port Handshake Verification (ASSUMPTION: We check localhost for port availability)
                port = initial_port
                while True:
                    in_use = False
                    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                        try:
                            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                            s.bind(("127.0.0.1", port))
                        except socket.error:
                            in_use = True
                    
                    if in_use:
                        # Check if it's already running our own MQTT broker or an active MQTT broker we can connect to
                        is_active_mqtt = False
                        try:
                            with socket.create_connection(("127.0.0.1", port), timeout=0.5) as s:
                                s.sendall(b"\x10\x0c\x00\x04MQTT\x04\x02\x00\x3c\x00\x00")
                                resp = s.recv(1)
                                if resp and resp[0] == 0x20:
                                    is_active_mqtt = True
                        except Exception:
                            pass
                        
                        if is_active_mqtt:
                            break
                        else:
                            port += 1
                            if port >= 48000:
                                port = 45000
                    else:
                        break
                self.port = port
        else:
            self.port = parsed.port or 0
        
        # For protocols that need a differentiation
        if self.password:
            # If password is explicitly provided, preserve exact username
            pass
        elif differentiator:
            suffix = f".{differentiator}"
            username = (username or "unknown") + suffix
        elif not username:
            username = "unknown"
        
        self.username = username

        if self.protocol == "pubsub":
            if parsed.port:
                raise ValueError("Port component not allowed for pubsub:// URLs")
            self.port = 0
            
            self.project_id = self.host
            path = parsed.path.lstrip('/')
            self.root_topic = path.split('/')[0] if path else "udmi_uufi"
            self.principal = f"{self.username}@"
            self.subscription = f"{self.root_topic}+{self.username}"
        else:
            self.project_id = "vibrant"
            self.root_topic = None
            self.principal = self.username
            self.subscription = None

        # path is prefix or root topic
        path = parsed.path.strip('/')
        self.prefix = path if path else ""

    def format_conn_spec(self):
        prefix_val = self.prefix if self.prefix else "None"
        return f"Conn spec: scheme={self.protocol}, host={self.host}, port={self.port}, principal={self.principal}, prefix={prefix_val}"

    def __str__(self):
        if self.protocol == "mqtt":
            parsed = urlparse(self.conn_str)
            port_part = f":{self.port}"
            prefix_part = parsed.path
            user_part = ""
            if parsed.username:
                if parsed.password:
                    user_part = f"{parsed.username}:{parsed.password}@"
                else:
                    user_part = f"{parsed.username}@"
            host = parsed.hostname or "localhost"
            return f"{self.protocol}://{user_part}{host}{port_part}{prefix_part}"
        return self.conn_str

def parse_conn_spec(conn_str, differentiator=None, is_passive=False):
    if conn_str is None:
        conn_str = get_default_conn_spec()
    return ConnSpec(conn_str, differentiator, is_passive=is_passive)

def get_default_conn_spec():
    branch = get_branch()
    return f"mqtt://{branch}@localhost/"

def get_branch():
    branch = "unknown"
    try:
        branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"], 
            stderr=subprocess.STDOUT
        ).decode().strip()
    except Exception:
        pass
    return branch

def get_default_registry_id():
    return os.environ.get("BUTLER_REGISTRY_ID", "default")

def split_device_id(device_id):
    if device_id and '/' in device_id:
        return device_id.split('/', 1)
    return get_default_registry_id(), device_id

def match_principal(p1, p2):
    if not p1 or not p2:
        return p1 == p2
    
    def get_base(p):
        return p.rstrip('@').split('.')[0]
    
    return get_base(p1) == get_base(p2)
