from urllib.parse import urlparse
import re
import os
import subprocess

class ConnSpec:
    def __init__(self, conn_str):
        self.conn_str = conn_str
        parsed = urlparse(conn_str)
        self.protocol = parsed.scheme
        
        # user@host:port
        self.username = parsed.username
        self.host = parsed.hostname or "localhost"
        self.port = parsed.port
        
        # path is prefix or root topic
        path = parsed.path.lstrip('/')
        self.prefix = path if path else None

        # PubSub specific
        if self.protocol == "pubsub":
            self.project_id = self.host
            self.root_topic = self.prefix or "udmi_uufi"
            self.principal = f"{self.username}@" if self.username else None
            self.subscription = f"{self.root_topic}+{self.username}" if self.username else self.root_topic
        else:
            self.project_id = None
            self.root_topic = None
            self.principal = self.username
            self.subscription = None

    def __str__(self):
        return self.conn_str

def parse_conn_spec(conn_str):
    return ConnSpec(conn_str)

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
