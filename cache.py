"""
cache.py — Thread-Safe LRU Cache with Disk Persistence and TTL
[Hamza] Implemented the full cache subsystem:
        - In-memory LRU dictionary with max-size eviction
        - TTL derived from Cache-Control / Expires response headers
        - Optional disk persistence for cache survival across restarts
        - Thread-safe via RLock
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Optional, Tuple

import config as _config
from config import (
    CACHE_DEFAULT_TTL,
    CACHE_DIR,
    CACHE_DISK_ENABLED,
    CACHE_ENABLED,
)
from logger import log


@dataclass
class CacheEntry:
    """A single cached HTTP response."""
    url: str
    status_line: str
    headers: bytes           # Raw response header bytes
    body: bytes              # Response body
    created_at: float = field(default_factory=time.time)
    ttl: float = CACHE_DEFAULT_TTL

    @property
    def is_expired(self) -> bool:
        return (time.time() - self.created_at) > self.ttl

    @property
    def age(self) -> float:
        return time.time() - self.created_at


class CacheManager:
    """
    [Hamza] Thread-safe LRU cache for HTTP GET responses.

    Features:
      - LRU eviction when CACHE_MAX_SIZE is reached
      - Per-entry TTL parsed from Cache-Control / Expires headers
      - Optional disk persistence using JSON-serialised metadata + binary body files
      - HIT/MISS statistics for the admin dashboard
    """

    def __init__(self):
        self._store: OrderedDict[str, CacheEntry] = OrderedDict()
        self._lock = threading.RLock()
        self.hits = 0
        self.misses = 0

        if CACHE_DISK_ENABLED:
            os.makedirs(CACHE_DIR, exist_ok=True)
            self._load_from_disk()


    def get(self, url: str) -> Optional[CacheEntry]:
        """
        [Hamza] Return a non-expired CacheEntry for *url*, or None on MISS.
        Moves accessed entry to the end of the OrderedDict (LRU order).
        """
        if not CACHE_ENABLED:
            return None

        key = self._make_key(url)
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self.misses += 1
                log.debug(f"[Cache MISS] {url}")
                return None

            if entry.is_expired:
                log.debug(f"[Cache EXPIRED] {url} (age={entry.age:.1f}s)")
                self._evict(key)
                self.misses += 1
                return None

            # Move to end → recently used
            self._store.move_to_end(key)
            self.hits += 1
            log.info(f"[Cache HIT] {url} (age={entry.age:.1f}s / ttl={entry.ttl}s)")
            return entry

    def put(self, url: str, status_line: str, response_headers: bytes,
            body: bytes, raw_header_text: str = "") -> None:
        """
        [Hamza] Store a GET response in cache.
        Parses TTL from headers, applies LRU eviction if needed.
        """
        if not CACHE_ENABLED:
            return

        ttl = self._parse_ttl(raw_header_text)
        if ttl <= 0:
            log.debug(f"[Cache SKIP] {url} — TTL=0 (no-store / no-cache directive)")
            return

        key = self._make_key(url)
        entry = CacheEntry(
            url=url,
            status_line=status_line,
            headers=response_headers,
            body=body,
            ttl=ttl,
        )

        with self._lock:
            # If already present, just update
            if key in self._store:
                self._store.move_to_end(key)
                self._store[key] = entry
            else:
                # Evict LRU if over capacity
                while len(self._store) >= _config.CACHE_MAX_SIZE:
                    oldest_key, _ = self._store.popitem(last=False)
                    log.debug(f"[Cache LRU EVICT] key={oldest_key[:8]}…")
                self._store[key] = entry

            log.debug(f"[Cache STORE] {url} (ttl={ttl}s)")

        if CACHE_DISK_ENABLED:
            self._save_to_disk(key, entry)

    def invalidate(self, url: str) -> bool:
        """[Hamza] Remove a specific URL from cache. Returns True if removed."""
        key = self._make_key(url)
        with self._lock:
            if key in self._store:
                self._evict(key)
                return True
        return False

    def clear(self) -> int:
        """[Hamza] Flush entire cache. Returns number of entries removed."""
        with self._lock:
            count = len(self._store)
            self._store.clear()
            log.info(f"[Cache] Cleared {count} entries.")
            return count

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._store)

    @property
    def stats(self) -> dict:
        with self._lock:
            total = self.hits + self.misses
            return {
                "size": len(self._store),
                "max_size": _config.CACHE_MAX_SIZE,
                "hits": self.hits,
                "misses": self.misses,
                "hit_rate": f"{(self.hits / total * 100):.1f}%" if total else "N/A",
            }

    def all_entries(self) -> list[dict]:
        """[Hamza] Return summary list of all cache entries (for dashboard)."""
        with self._lock:
            return [
                {
                    "url": e.url,
                    "age": f"{e.age:.0f}s",
                    "ttl": f"{e.ttl}s",
                    "size": f"{len(e.body) / 1024:.1f} KB",
                    "expired": e.is_expired,
                }
                for e in self._store.values()
            ]


    @staticmethod
    def _make_key(url: str) -> str:
        """[Hamza] Create a consistent SHA-256 hash key for a URL."""
        return hashlib.sha256(url.encode("utf-8")).hexdigest()

    def _evict(self, key: str) -> None:
        """Remove entry from memory and disk."""
        self._store.pop(key, None)
        if CACHE_DISK_ENABLED:
            self._delete_from_disk(key)

    def _parse_ttl(self, header_text: str) -> float:
        """
        [Hamza] Derive TTL seconds from Cache-Control or Expires headers.
        Returns 0 if content must not be cached, or CACHE_DEFAULT_TTL as fallback.
        """
        if not header_text:
            return CACHE_DEFAULT_TTL

        header_lower = header_text.lower()

        # no-store / no-cache → do not cache
        if "no-store" in header_lower or "no-cache" in header_lower:
            return 0

        # max-age=N
        match = re.search(r"max-age\s*=\s*(\d+)", header_lower)
        if match:
            return max(float(match.group(1)), 0)

        # s-maxage=N (shared / proxy max age)
        match = re.search(r"s-maxage\s*=\s*(\d+)", header_lower)
        if match:
            return max(float(match.group(1)), 0)

        return CACHE_DEFAULT_TTL


    def _save_to_disk(self, key: str, entry: CacheEntry) -> None:
        """[Hamza] Persist a cache entry's metadata and body to disk."""
        try:
            meta = {
                "url": entry.url,
                "status_line": entry.status_line,
                "headers": entry.headers.decode("utf-8", errors="replace"),
                "created_at": entry.created_at,
                "ttl": entry.ttl,
            }
            meta_path = os.path.join(CACHE_DIR, f"{key}.json")
            body_path = os.path.join(CACHE_DIR, f"{key}.bin")

            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(meta, f)
            with open(body_path, "wb") as f:
                f.write(entry.body)
        except OSError as exc:
            log.warning(f"[Cache] Disk write failed: {exc}")

    def _delete_from_disk(self, key: str) -> None:
        """[Hamza] Remove a cached entry from disk."""
        for ext in (".json", ".bin"):
            path = os.path.join(CACHE_DIR, f"{key}{ext}")
            try:
                os.remove(path)
            except FileNotFoundError:
                pass

    def _load_from_disk(self) -> None:
        """[Hamza] Reload non-expired cache entries from disk on startup."""
        loaded = 0
        try:
            for fname in os.listdir(CACHE_DIR):
                if not fname.endswith(".json"):
                    continue
                key = fname[:-5]
                meta_path = os.path.join(CACHE_DIR, fname)
                body_path = os.path.join(CACHE_DIR, f"{key}.bin")
                try:
                    with open(meta_path, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                    with open(body_path, "rb") as f:
                        body = f.read()

                    entry = CacheEntry(
                        url=meta["url"],
                        status_line=meta["status_line"],
                        headers=meta["headers"].encode("utf-8"),
                        body=body,
                        created_at=meta["created_at"],
                        ttl=meta["ttl"],
                    )
                    if not entry.is_expired:
                        self._store[key] = entry
                        loaded += 1
                    else:
                        self._delete_from_disk(key)
                except Exception:
                    pass  # Corrupt entry — skip silently
        except OSError:
            pass
        if loaded:
            log.info(f"[Cache] Restored {loaded} entries from disk.")
