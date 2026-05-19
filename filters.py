"""
filters.py — Blacklist / Whitelist and Rate Limiting

[Mohammed Hazime] Implemented domain/IP filtering and per-client rate limiting.
      Reads blacklist.txt and whitelist.txt at startup and can
      reload them at runtime without restarting the proxy.
"""

from __future__ import annotations

import ipaddress
import socket
import threading
import time
from collections import defaultdict, deque
from typing import Set, Tuple

import config as _config
from config import (
    BLACKLIST_FILE,
    WHITELIST_FILE,
)
from logger import log


class FilterManager:
    """
    [Mohammed Hazime] Manages blacklist/whitelist filtering and per-IP rate limiting.

    Blacklist takes precedence over whitelist.
    When whitelist mode is active, only explicitly listed hosts are allowed.
    """

    def __init__(self):
        self._lock = threading.RLock()

        self._blacklist: Set[str] = set()
        self._whitelist: Set[str] = set()

        # Rate limiting: IP → deque of request timestamps
        self._rate_map: dict[str, deque] = defaultdict(deque)

        self.blocked_count = 0
        self.rate_limited_count = 0

        self._load_blacklist()
        self._load_whitelist()

    def is_blocked(self, host: str) -> Tuple[bool, str]:
        """
        [Mohammed Hazime] Check whether a target host/domain should be blocked.

        Returns (blocked: bool, reason: str).
        Checks:
          1. Blacklist (domain match or IP match)
          2. Whitelist enforcement (if WHITELIST_ENABLED)
        """
        normalized = self._normalize(host)

        with self._lock:
            # ── Blacklist Check ─────────────────────────────────────────
            if self._matches_list(normalized, self._blacklist):
                self.blocked_count += 1
                log.warning(f"[Filter BLOCKED] {host} — blacklisted")
                return True, f"{host} is blacklisted"

            # Try to resolve to IP and check IP-based blacklist entries
            try:
                ip = socket.gethostbyname(host)
                if self._ip_in_list(ip, self._blacklist):
                    self.blocked_count += 1
                    log.warning(f"[Filter BLOCKED] {host} ({ip}) — IP blacklisted")
                    return True, f"{host} is blacklisted (IP: {ip})"
            except socket.gaierror:
                pass  # Cannot resolve — continue

            # ── Whitelist Check (if enabled) ────────────────────────────
            if _config.WHITELIST_ENABLED and self._whitelist:
                if not self._matches_list(normalized, self._whitelist):
                    self.blocked_count += 1
                    log.warning(f"[Filter BLOCKED] {host} — not in whitelist")
                    return True, f"{host} is not whitelisted"

        return False, ""

    def is_rate_limited(self, client_ip: str) -> bool:
        """
        [Mohammed Hazime] Sliding-window rate limiter. Returns True if the client
        has exceeded RATE_LIMIT_MAX_REQUESTS within RATE_LIMIT_WINDOW seconds.
        """
        if not _config.RATE_LIMIT_ENABLED:
            return False

        now = time.time()
        window_start = now - _config.RATE_LIMIT_WINDOW

        with self._lock:
            q = self._rate_map[client_ip]

            # Drop timestamps outside the window
            while q and q[0] < window_start:
                q.popleft()

            if len(q) >= _config.RATE_LIMIT_MAX_REQUESTS:
                self.rate_limited_count += 1
                log.warning(
                    f"[RateLimit] {client_ip} exceeded {_config.RATE_LIMIT_MAX_REQUESTS} "
                    f"req/{_config.RATE_LIMIT_WINDOW}s"
                )
                return True

            q.append(now)
            return False

    def reload(self) -> None:
        """[Mohammed Hazime] Hot-reload filter files without restarting the proxy."""
        with self._lock:
            self._load_blacklist()
            self._load_whitelist()
        log.info("[Filter] Reloaded blacklist and whitelist.")

    def add_to_blacklist(self, entry: str) -> None:
        """[Mohammed Hazime] Add an entry to the in-memory blacklist and persist to file."""
        with self._lock:
            self._blacklist.add(self._normalize(entry))
            self._persist(BLACKLIST_FILE, self._blacklist)

    def remove_from_blacklist(self, entry: str) -> bool:
        """[Mohammed Hazime] Remove an entry from the blacklist. Returns True if removed."""
        with self._lock:
            normalized = self._normalize(entry)
            if normalized in self._blacklist:
                self._blacklist.discard(normalized)
                self._persist(BLACKLIST_FILE, self._blacklist)
                return True
        return False

    @property
    def blacklist(self) -> list[str]:
        with self._lock:
            return sorted(self._blacklist)

    @property
    def whitelist(self) -> list[str]:
        with self._lock:
            return sorted(self._whitelist)

    def _load_blacklist(self) -> None:
        self._blacklist = self._read_file(BLACKLIST_FILE)
        log.info(f"[Filter] Loaded {len(self._blacklist)} blacklist entries.")

    def _load_whitelist(self) -> None:
        self._whitelist = self._read_file(WHITELIST_FILE)
        log.info(f"[Filter] Loaded {len(self._whitelist)} whitelist entries.")

    @staticmethod
    def _read_file(path: str) -> Set[str]:
        """[Mohammed Hazime] Read a filter file, ignoring blank lines and # comments."""
        entries: Set[str] = set()
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        entries.add(line.lower())
        except FileNotFoundError:
            pass
        return entries

    @staticmethod
    def _persist(path: str, entries: Set[str]) -> None:
        """[Mohammed Hazime] Write the current in-memory set back to the filter file."""
        try:
            with open(path, "w", encoding="utf-8") as f:
                for entry in sorted(entries):
                    f.write(entry + "\n")
        except OSError as exc:
            log.error(f"[Filter] Could not persist to {path}: {exc}")

    @staticmethod
    def _normalize(host: str) -> str:
        """[Mohammed Hazime] Lowercase and strip port from a host string."""
        host = host.lower().strip()
        if ":" in host:
            host = host.rsplit(":", 1)[0]
        return host

    @staticmethod
    def _matches_list(host: str, entries: Set[str]) -> bool:
        """
        [Mohammed Hazime] Check if *host* matches any entry in the set.
        Supports:
          - Exact match: example.com
          - Wildcard subdomain: *.example.com  or  .example.com
        """
        if host in entries:
            return True
        # Check wildcard / suffix entries
        parts = host.split(".")
        for i in range(len(parts) - 1):
            candidate = "." + ".".join(parts[i:])  # .example.com
            if candidate in entries:
                return True
            candidate2 = "*." + ".".join(parts[i:])  # *.example.com
            if candidate2 in entries:
                return True
        return False

    @staticmethod
    def _ip_in_list(ip_str: str, entries: Set[str]) -> bool:
        """[Mohammed Hazime] Check if IP falls in any CIDR or exact IP entry in the set."""
        try:
            ip = ipaddress.ip_address(ip_str)
            for entry in entries:
                try:
                    network = ipaddress.ip_network(entry, strict=False)
                    if ip in network:
                        return True
                except ValueError:
                    pass
        except ValueError:
            pass
        return False
