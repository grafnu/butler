from urllib.parse import urlparse
import re
import os
import subprocess

class ConnSpec:
    def __init__(self, conn_str, differentiator=None):
        self.conn_str = conn_str
        parsed = urlparse(conn_str)
        self.protocol = parsed.scheme
        
        # user@host:port
        username = parsed.username
        self.host = parsed.hostname or "localhost"
        self.port = parsed.port
        
        if self.protocol == "pubsub":
            if self.port:
                raise ValueError("Port component not allowed for pubsub:// URLs")
            if username and "." in username:
                raise ValueError("Manual differentiator (dot) not allowed in username for pubsub:// URLs")
            
            if differentiator:
                suffix = f".{differentiator}"
                username = (username or "unknown") + suffix
            
            self.username = username
            self.project_id = self.host
            path = parsed.path.lstrip('/')
            self.root_topic = path if path else "udmi_uufi"
            self.principal = f"{self.username}@" if self.username else None
            self.subscription = f"{self.root_topic}+{self.username}" if self.username else self.root_topic
        else:
            self.username = username
            self.project_id = None
            self.root_topic = None
            self.principal = self.username
            self.subscription = None

        # path is prefix or root topic
        path = parsed.path.lstrip('/')
        self.prefix = path if path else None

    def __str__(self):
        return self.conn_str

def parse_conn_spec(conn_str, differentiator=None):
    if conn_str is None:
        conn_str = get_default_conn_spec()
    return ConnSpec(conn_str, differentiator)

def get_default_conn_spec():
    if "BUTLER_CONN_SPEC" in os.environ:
        return os.environ["BUTLER_CONN_SPEC"]
    
    branch = "unknown"
    try:
        branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"], 
            stderr=subprocess.STDOUT
        ).decode().strip()
    except Exception:
        pass
    
    return f"mqtt://{branch}@localhost/"
