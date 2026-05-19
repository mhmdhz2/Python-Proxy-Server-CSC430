"""
proxy_server.py — Core Proxy Server
=====================================
[Mohammed Hazime] Implemented the main proxy server engine:
      - Multi-threaded client handling
      - HTTP request forwarding
      - HTTPS CONNECT tunneling
      - Cache integration
      - Filter and rate-limit enforcement
      - Graceful shutdown
"""

from __future__ import annotations

import select
import socket
import threading
import time
from typing import Optional

from cache import CacheManager
from config import (
    BUFFER_SIZE,
    HTTPS_TUNNEL_ENABLED,
    MAX_CONNECTIONS,
    PROXY_HOST,
    PROXY_PORT,
    SHUTDOWN_TIMEOUT,
    SOCKET_TIMEOUT,
)
from filters import FilterManager
from logger import log
from request_parser import RequestParser
from utils import (
    StatsTracker,
    build_200_connection_established,
    build_403_response,
    build_429_response,
    build_502_response,
    recv_all,
    safe_close,
)


class ProxyServer:
    """
    [Mohammed Hazime] The main proxy server. Creates a listening TCP socket and dispatches
    each incoming connection to a dedicated thread.
    """

    def __init__(
        self,
        host: str = PROXY_HOST,
        port: int = PROXY_PORT,
        cache: Optional[CacheManager] = None,
        filters: Optional[FilterManager] = None,
        stats: Optional[StatsTracker] = None,
    ):
        self.host = host
        self.port = port
        self.cache = cache or CacheManager()
        self.filters = filters or FilterManager()
        self.stats = stats or StatsTracker()
        self.parser = RequestParser()

        self._server_sock: Optional[socket.socket] = None
        self._running = False
        self._threads: list[threading.Thread] = []
        self._lock = threading.Lock()



    def start(self) -> None:
        """[Ali] Bind and start listening. Blocks until stop() is called."""
        self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_sock.bind((self.host, self.port))
        self._server_sock.listen(MAX_CONNECTIONS)
        self._server_sock.settimeout(1.0)  # Allow periodic shutdown checks
        self._running = True

        log.info(
            f"[Proxy] Started on {self.host}:{self.port} "
            f"| Cache: {'ON' if True else 'OFF'} "
            f"| HTTPS Tunnel: {'ON' if HTTPS_TUNNEL_ENABLED else 'OFF'}"
        )

        while self._running:
            try:
                client_sock, client_addr = self._server_sock.accept()
                client_sock.settimeout(SOCKET_TIMEOUT)
                t = threading.Thread(
                    target=self._handle_client,
                    args=(client_sock, client_addr),
                    daemon=True,
                )
                with self._lock:
                    self._threads.append(t)
                t.start()
            except socket.timeout:
                continue
            except OSError:
                if self._running:
                    log.error("[Proxy] Server socket error.")
                break

        self._shutdown()

    def stop(self) -> None:
        """[Ali] Signal the server to stop accepting new connections."""
        log.info("[Proxy] Stopping...")
        self._running = False



    def _handle_client(
        self, client_sock: socket.socket, client_addr: tuple
    ) -> None:
        """
        [Ali] Entry point for each client thread.
        Reads the initial request, applies filters, and routes to
        the appropriate handler (HTTP forward or HTTPS tunnel).
        """
        client_ip, client_port = client_addr
        self.stats.connection_open()

        try:
            if self.filters.is_rate_limited(client_ip):
                client_sock.sendall(build_429_response())
                return

            raw_request = b""
            try:
                while b"\r\n\r\n" not in raw_request:
                    chunk = client_sock.recv(BUFFER_SIZE)
                    if not chunk:
                        return
                    raw_request += chunk
                    if len(raw_request) > 1_048_576:  # 1 MB header limit
                        return
            except socket.timeout:
                return

            req = self.parser.parse(raw_request)
            if not req.is_valid:
                log.warning(f"[Proxy] Invalid request from {client_ip}: {req.error}")
                return

            log.info(
                f"[{client_ip}:{client_port}] {req.method} {req.url or req.host}"
            )
            self.stats.record_request(req.host, is_https=req.is_connect)

            blocked, reason = self.filters.is_blocked(req.host)
            if blocked:
                client_sock.sendall(build_403_response(req.host, reason))
                return

            if req.is_connect:
                if HTTPS_TUNNEL_ENABLED:
                    self._handle_https_tunnel(client_sock, req, client_ip)
                else:
                    client_sock.sendall(
                        build_403_response(req.host, "HTTPS tunneling is disabled.")
                    )
            else:
                self._handle_http(client_sock, req, client_ip, raw_request)

        except Exception as exc:
            log.error(f"[Proxy] Unhandled error for {client_ip}: {exc}", exc_info=True)
            self.stats.record_error()
        finally:
            safe_close(client_sock)
            self.stats.connection_close()


    def _handle_http(
        self,
        client_sock: socket.socket,
        req,
        client_ip: str,
        raw_request: bytes,
    ) -> None:
        """
        [Ali] Forward an HTTP request and return the response.
        Checks cache on GET; stores cacheable responses.
        """
        if req.method == "GET":
            entry = self.cache.get(req.url)
            if entry:
                # Serve from cache
                response = entry.headers + b"\r\n" + entry.body
                try:
                    client_sock.sendall(response)
                except OSError:
                    pass
                self.stats.record_bytes(sent=len(response), received=0)
                log.info(
                    f"[Cache HIT] {req.url} — served {len(entry.body)} bytes from cache"
                )
                return

        target_sock: Optional[socket.socket] = None
        try:
            target_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            target_sock.settimeout(SOCKET_TIMEOUT)
            target_sock.connect((req.host, req.port))

            # Send modified request headers + any body
            forward_data = req.raw_headers
            if req.body:
                forward_data += req.body
            target_sock.sendall(forward_data)

            # Read full response
            response = recv_all(target_sock, timeout=SOCKET_TIMEOUT)

        except (socket.timeout, ConnectionRefusedError, OSError) as exc:
            log.error(f"[Proxy] Cannot connect to {req.host}:{req.port} — {exc}")
            client_sock.sendall(build_502_response(str(exc)))
            self.stats.record_error()
            return
        finally:
            safe_close(target_sock)

        if not response:
            client_sock.sendall(build_502_response("Empty response from target."))
            return

        status_line = "HTTP/1.1 200 OK"
        try:
            status_line = response.split(b"\r\n", 1)[0].decode("utf-8", errors="replace")
        except Exception:
            pass

        log.info(f"[{client_ip}] ← {status_line} ({len(response)} bytes)")

        if req.method == "GET" and response:
            status_code = 0
            try:
                status_code = int(status_line.split()[1])
            except (IndexError, ValueError):
                pass

            if 200 <= status_code < 300 and b"\r\n\r\n" in response:
                raw_headers, body = response.split(b"\r\n\r\n", 1)
                header_text = raw_headers.decode("utf-8", errors="replace")
                self.cache.put(
                    url=req.url,
                    status_line=status_line,
                    response_headers=raw_headers,
                    body=body,
                    raw_header_text=header_text,
                )

        # ── Send Response to Client ───────────────────────────────────────
        try:
            client_sock.sendall(response)
            self.stats.record_bytes(sent=len(response), received=len(forward_data))
        except OSError as exc:
            log.warning(f"[Proxy] Client send failed: {exc}")

  

    def _handle_https_tunnel(
        self,
        client_sock: socket.socket,
        req,
        client_ip: str,
    ) -> None:
        """
        [Mohammed Hazime] Open a raw TCP tunnel for HTTPS CONNECT requests.

        The proxy does NOT decrypt TLS — it simply relays bytes between
        the client and the target server. This is the safe, RFC-7231-compliant
        approach to HTTPS proxying.
        """
        target_sock: Optional[socket.socket] = None
        try:
            target_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            target_sock.settimeout(SOCKET_TIMEOUT)
            target_sock.connect((req.host, req.port))
        except (OSError, socket.timeout) as exc:
            log.error(f"[HTTPS] Cannot connect to {req.host}:{req.port} — {exc}")
            client_sock.sendall(build_502_response(str(exc)))
            safe_close(target_sock)
            return

        # Acknowledge the tunnel to the browser
        client_sock.sendall(build_200_connection_established())
        log.info(f"[HTTPS TUNNEL] {client_ip} ↔ {req.host}:{req.port}")

        client_sock.setblocking(False)
        target_sock.setblocking(False)
        total_bytes = 0

        try:
            while True:
                readable, _, exceptional = select.select(
                    [client_sock, target_sock], [], [client_sock, target_sock], 5.0
                )
                if exceptional or (not readable):
                    break

                for sock in readable:
                    other = target_sock if sock is client_sock else client_sock
                    try:
                        data = sock.recv(BUFFER_SIZE)
                        if not data:
                            return  # Connection closed
                        other.sendall(data)
                        total_bytes += len(data)
                    except (OSError, ConnectionResetError):
                        return
        finally:
            safe_close(target_sock)
            self.stats.record_bytes(sent=total_bytes // 2, received=total_bytes // 2)
            log.debug(f"[HTTPS TUNNEL] {req.host} closed — {total_bytes} bytes relayed")


    def _shutdown(self) -> None:
        """[Mohammed Hazime] Wait for active threads and close the server socket."""
        safe_close(self._server_sock)
        log.info(f"[Proxy] Waiting up to {SHUTDOWN_TIMEOUT}s for active threads…")
        deadline = time.time() + SHUTDOWN_TIMEOUT
        with self._lock:
            threads = list(self._threads)
        for t in threads:
            remaining = deadline - time.time()
            if remaining > 0:
                t.join(timeout=remaining)
        log.info("[Proxy] Shutdown complete.")
