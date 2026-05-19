"""
config.py — Central Configuration for the Caching Proxy Server
[Hamza] Defined all global settings and configurable parameters.

All runtime settings are loaded here. Modify this file to tune
proxy behavior without touching other source files.
"""

import os

PROXY_HOST = "0.0.0.0"       # Listen on all interfaces
PROXY_PORT = 8888             # Default proxy port
BUFFER_SIZE = 65536           # 64 KB socket read buffer
SOCKET_TIMEOUT = 15           # Seconds before a socket times out
MAX_CONNECTIONS = 200         # Backlog for server.listen()

CACHE_ENABLED = True
CACHE_MAX_SIZE = 500          # Maximum number of cached entries (LRU eviction)
CACHE_DEFAULT_TTL = 60        # Seconds — used when no Cache-Control / Expires header
CACHE_DISK_ENABLED = True     # Persist cache to disk
CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache_store")

RATE_LIMIT_ENABLED = True
RATE_LIMIT_MAX_REQUESTS = 100  # Requests per window per IP
RATE_LIMIT_WINDOW = 60         # Window in seconds

BLACKLIST_FILE = os.path.join(os.path.dirname(__file__), "blacklist.txt")
WHITELIST_FILE = os.path.join(os.path.dirname(__file__), "whitelist.txt")
WHITELIST_ENABLED = False      # Set True to enforce whitelist (block everything not listed)

LOG_FILE = os.path.join(os.path.dirname(__file__), "logs", "proxy.log")
LOG_LEVEL = "DEBUG"            # DEBUG | INFO | WARNING | ERROR
LOG_MAX_BYTES = 10_485_760     # 10 MB before log rotation
LOG_BACKUP_COUNT = 5

DASHBOARD_ENABLED = True
DASHBOARD_HOST = "127.0.0.1"
DASHBOARD_PORT = 5000
DASHBOARD_SECRET = "changeme"  # Set a strong secret in production

HTTPS_TUNNEL_ENABLED = True   # Enable CONNECT method tunneling (no decryption)

SHUTDOWN_TIMEOUT = 5           # Seconds to wait for threads to finish on shutdown
