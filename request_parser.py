"""
request_parser.py — HTTP Request Parser
[Hamza] Implemented full HTTP request parsing: method, URL, host,
        port, headers, and header modification for proxy forwarding.
"""

from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple
from logger import log


@dataclass
class ParsedRequest:
    """Structured representation of a parsed HTTP request."""
    method: str = ""
    url: str = ""
    path: str = "/"
    version: str = "HTTP/1.1"
    host: str = ""
    port: int = 80
    headers: Dict[str, str] = field(default_factory=dict)
    raw_headers: bytes = b""
    body: bytes = b""
    is_connect: bool = False
    is_valid: bool = False
    error: str = ""


class RequestParser:
    """
    [Hamza] Parses raw HTTP/HTTPS request bytes into a structured ParsedRequest.

    Handles:
      - Standard HTTP methods: GET, POST, HEAD, PUT, DELETE, OPTIONS
      - CONNECT method for HTTPS tunneling
      - Header normalization and proxy-specific header modification
    """

    # Headers that must be removed before forwarding
    HOP_BY_HOP = {
        "proxy-connection",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
        "keep-alive",
    }

    def parse(self, raw_data: bytes) -> ParsedRequest:
        """
        [Hamza] Parse raw request bytes and return a ParsedRequest.
        Returns a ParsedRequest with is_valid=False on parse failure.
        """
        req = ParsedRequest()

        try:
            # Split header section from optional body
            if b"\r\n\r\n" in raw_data:
                header_section, req.body = raw_data.split(b"\r\n\r\n", 1)
            else:
                header_section = raw_data
                req.body = b""

            lines = header_section.decode("utf-8", errors="replace").split("\r\n")
            if not lines:
                req.error = "Empty request"
                return req

            request_line = lines[0].strip()
            parts = request_line.split(" ", 2)
            if len(parts) < 2:
                req.error = f"Malformed request line: {request_line!r}"
                return req

            req.method = parts[0].upper()
            req.url = parts[1]
            req.version = parts[2] if len(parts) == 3 else "HTTP/1.1"

            if req.method == "CONNECT":
                req.is_connect = True
                host_port = req.url  # format: host:port
                req.host, req.port = self._split_host_port(host_port, default_port=443)
                req.is_valid = True
                return req

            req.host, req.port, req.path = self._parse_url(req.url)

            for line in lines[1:]:
                if not line.strip():
                    continue
                if ":" not in line:
                    continue
                key, _, value = line.partition(":")
                req.headers[key.strip().lower()] = value.strip()

            if not req.host and "host" in req.headers:
                req.host, req.port = self._split_host_port(
                    req.headers["host"], default_port=80
                )

            req.raw_headers = self._build_forwarding_headers(req)
            req.is_valid = bool(req.host)

            if not req.is_valid:
                req.error = "Could not determine target host"

        except Exception as exc:
            req.error = str(exc)
            log.warning(f"[RequestParser] Parse error: {exc}")

        return req


    def _parse_url(self, url: str) -> Tuple[str, int, str]:
        """[Hamza] Extract host, port, and path from an absolute or relative URL."""
        host = ""
        port = 80
        path = "/"

        # Absolute URL: http://host:port/path
        match = re.match(
            r"^https?://([^/:]+)(?::(\d+))?(.*)?$", url, re.IGNORECASE
        )
        if match:
            host = match.group(1)
            port = int(match.group(2)) if match.group(2) else 80
            path = match.group(3) or "/"
            if not path:
                path = "/"
        else:
            # Relative URL — host must come from Host header
            path = url

        return host, port, path

    def _split_host_port(self, host_port: str, default_port: int = 80) -> Tuple[str, int]:
        """[Hamza] Split 'host:port' string into (host, port) tuple."""
        if ":" in host_port:
            host, _, port_str = host_port.rpartition(":")
            try:
                return host, int(port_str)
            except ValueError:
                pass
        return host_port, default_port

    def _build_forwarding_headers(self, req: ParsedRequest) -> bytes:
        """
        [Hamza] Rebuild the request header block suitable for forwarding.
        - Sets Host header
        - Sets Connection: close (disable keep-alive to target)
        - Strips hop-by-hop and proxy-specific headers
        """
        lines = [f"{req.method} {req.path} {req.version}"]

        host_header = req.host
        if req.port != 80:
            host_header = f"{req.host}:{req.port}"
        lines.append(f"Host: {host_header}")

        for key, value in req.headers.items():
            if key.lower() in self.HOP_BY_HOP:
                continue
            if key.lower() == "host":
                continue  # Already set above
            lines.append(f"{key.title()}: {value}")

        lines.append("Connection: close")
        lines.append("")  # Blank line separating headers from body
        lines.append("")

        return "\r\n".join(lines).encode("utf-8")
