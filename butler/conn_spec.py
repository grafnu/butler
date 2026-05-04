from urllib.parse import urlparse
import re

class ConnSpec:
    def __init__(self, conn_str):
        self.conn_str = conn_str
        parsed = urlparse(conn_str)
        self.protocol = parsed.scheme
        
        # user@host:port
        self.username = parsed.username
        self.host = parsed.hostname or "localhost"
        self.port = parsed.port
        
        # path is prefix
        path = parsed.path.lstrip('/')
        self.prefix = path if path else None

    def __str__(self):
        return self.conn_str

def parse_conn_spec(conn_str):
    return ConnSpec(conn_str)
