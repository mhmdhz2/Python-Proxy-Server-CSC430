"""
utils.py — Shared Utility Functions
[Hamza] Utility helpers used across multiple modules:
        HTTP response builders, relay helpers, stats tracker.
"""

from __future__ import annotations

import socket
import threading
import time
from typing import Optional

from logger import log



class StatsTracker:
    """[Hamza] Global request/response statistics shared across threads."""

    def __init__(self):
        self._lock = threading.Lock()
        self.total_requests = 0
        self.total_bytes_sent = 0
        self.total_bytes_received = 0
        self.active_connections = 0
        self.domain_counts: dict[str, int] = {}
        self.start_time = time.time()
        self.error_count = 0
        self.https_count = 0

    def record_request(self, host: str, is_https: bool = False) -> None:
        with self._lock:
            self.total_requests += 1
            if is_https:
                self.https_count += 1
            self.domain_counts[host] = self.domain_counts.get(host, 0) + 1

    def record_bytes(self, sent: int, received: int) -> None:
        with self._lock:
            self.total_bytes_sent += sent
            self.total_bytes_received += received

    def connection_open(self) -> None:
        with self._lock:
            self.active_connections += 1

    def connection_close(self) -> None:
        with self._lock:
            self.active_connections = max(0, self.active_connections - 1)

    def record_error(self) -> None:
        with self._lock:
            self.error_count += 1

    @property
    def uptime(self) -> str:
        elapsed = int(time.time() - self.start_time)
        h, m, s = elapsed // 3600, (elapsed % 3600) // 60, elapsed % 60
        return f"{h:02d}:{m:02d}:{s:02d}"

    @property
    def top_domains(self) -> list[tuple[str, int]]:
        with self._lock:
            return sorted(self.domain_counts.items(), key=lambda x: x[1], reverse=True)[:10]

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "total_requests": self.total_requests,
                "active_connections": self.active_connections,
                "https_tunnels": self.https_count,
                "bytes_sent": self.total_bytes_sent,
                "bytes_received": self.total_bytes_received,
                "error_count": self.error_count,
                "uptime": self.uptime,
                "top_domains": list(self.top_domains),
            }


def build_403_response(host: str, reason: str = "") -> bytes:
    """[Hamza] Return a custom HTTP 403 Forbidden response page."""
    body = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <title>403 Forbidden — Proxy</title>
  <style>
    body {{ font-family: Arial, sans-serif; background: #1a1a2e; color: #e0e0e0;
           display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }}
    .card {{ background: #16213e; padding: 40px 60px; border-radius: 12px;
             box-shadow: 0 8px 32px rgba(0,0,0,0.4); text-align: center; }}
    h1 {{ color: #e94560; font-size: 3rem; margin: 0 0 8px; }}
    h2 {{ font-size: 1.4rem; color: #a8a8b3; margin: 0 0 20px; }}
    p  {{ color: #a8a8b3; max-width: 400px; line-height: 1.6; }}
    code {{ background: #0f3460; padding: 2px 8px; border-radius: 4px; color: #e94560; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>403</h1>
    <h2>Access Forbidden</h2>
    <p>The proxy has blocked access to <code>{host}</code>.</p>
    <p>{reason}</p>
    <p>Contact your network administrator if you believe this is a mistake.</p>
  </div>
</body>
</html>""".encode("utf-8")

    header = (
        f"HTTP/1.1 403 Forbidden\r\n"
        f"Content-Type: text/html; charset=utf-8\r\n"
        f"Content-Length: {len(body)}\r\n"
        f"Connection: close\r\n"
        f"X-Proxy: CachingProxy/1.0\r\n"
        f"\r\n"
    ).encode("utf-8")

    return header + body


def build_429_response() -> bytes:
    """[Hamza] Return an HTTP 429 Too Many Requests response."""
    body = b"<html><body><h1>429 Too Many Requests</h1><p>Rate limit exceeded.</p></body></html>"
    header = (
        f"HTTP/1.1 429 Too Many Requests\r\n"
        f"Content-Type: text/html\r\n"
        f"Content-Length: {len(body)}\r\n"
        f"Retry-After: 60\r\n"
        f"Connection: close\r\n\r\n"
    ).encode("utf-8")
    return header + body


def build_502_response(detail: str = "") -> bytes:
    """[Hamza] Return an HTTP 502 Bad Gateway response."""
    body = f"<html><body><h1>502 Bad Gateway</h1><p>{detail}</p></body></html>".encode()
    header = (
        f"HTTP/1.1 502 Bad Gateway\r\n"
        f"Content-Type: text/html\r\n"
        f"Content-Length: {len(body)}\r\n"
        f"Connection: close\r\n\r\n"
    ).encode("utf-8")
    return header + body


def build_200_connection_established() -> bytes:
    """[Ali] 200 response sent to client confirming HTTPS tunnel is open."""
    return b"HTTP/1.1 200 Connection Established\r\nProxy-Agent: CachingProxy/1.0\r\n\r\n"



def relay_data(src: socket.socket, dst: socket.socket,
               stats: Optional[StatsTracker] = None,
               direction: str = "→") -> int:
    """
    [Ali] Read all available data from *src* and forward to *dst*.
    Returns total bytes transferred.
    Used by the HTTPS tunnel to relay encrypted traffic bidirectionally.
    """
    total = 0
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
            total += len(data)
            if stats:
                if direction == "→":
                    stats.record_bytes(sent=len(data), received=0)
                else:
                    stats.record_bytes(sent=0, received=len(data))
    except (OSError, ConnectionResetError):
        pass
    return total


def safe_close(sock: Optional[socket.socket]) -> None:
    """[Hamza] Close a socket, swallowing any errors."""
    if sock:
        try:
            sock.close()
        except OSError:
            pass


def recv_all(sock: socket.socket, timeout: float = 10.0) -> bytes:
    """
    [Hamza] Read the full HTTP response from *sock* until the connection closes.
    Handles chunked streaming of large responses.
    """
    sock.settimeout(timeout)
    data = b""
    try:
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            data += chunk
    except socket.timeout:
        pass
    except OSError:
        pass
    return data
