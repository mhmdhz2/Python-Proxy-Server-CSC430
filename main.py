"""
main.py — Entry Point for the Caching Proxy Server

[Mohammed Hazime] Wires all components together, handles SIGINT/SIGTERM for
      graceful shutdown, and optionally launches the admin dashboard.

Usage:
    python main.py                        # Default port 8888
    python main.py --port 9999            # Custom port
    python main.py --no-dashboard         # Skip Flask dashboard
    python main.py --port 8888 --host 0.0.0.0
"""

import argparse
import signal
import sys
import time

from cache import CacheManager
from config import (
    DASHBOARD_ENABLED,
    PROXY_HOST,
    PROXY_PORT,
)
from dashboard import run_dashboard
from filters import FilterManager
from logger import log
from proxy_server import ProxyServer
from utils import StatsTracker

_cache   = CacheManager()
_filters = FilterManager()
_stats   = StatsTracker()
_proxy: ProxyServer = None  # type: ignore


def _handle_shutdown(signum, frame):
    """[Ali] Graceful shutdown handler for Ctrl-C / SIGTERM."""
    log.info(f"\n[Main] Signal {signum} received — shutting down…")
    if _proxy:
        _proxy.stop()
    sys.exit(0)


def main():
    global _proxy

    parser = argparse.ArgumentParser(description="Caching Proxy Server — CSC 430")
    parser.add_argument("--host",         default=PROXY_HOST, help="Bind address")
    parser.add_argument("--port", type=int, default=PROXY_PORT, help="Listen port")
    parser.add_argument("--no-dashboard", action="store_true",  help="Disable admin dashboard")
    args = parser.parse_args()

    print("\n" + "═" * 60)
    print("   🛡  Caching Proxy Server  —  CSC 430 Computer Networks")
    print("   Lebanese American University")
    print("═" * 60)
    print(f"   Proxy address : http://localhost:{args.port}")
    print(f"   Dashboard     : http://localhost:5000")
    print(f"   Log file      : logs/proxy.log")
    print("   Press Ctrl-C to stop.\n")

    # ── Signal Handlers ────────────────────────────────────────────────────
    signal.signal(signal.SIGINT,  _handle_shutdown)
    signal.signal(signal.SIGTERM, _handle_shutdown)

    if DASHBOARD_ENABLED and not args.no_dashboard:
        try:
            t = run_dashboard(_cache, _filters, _stats)
            if t:
                log.info("[Main] Admin dashboard started at http://127.0.0.1:5000")
            else:
                log.warning("[Main] Dashboard unavailable (Flask not installed?)")
        except Exception as exc:
            log.warning(f"[Main] Dashboard failed to start: {exc}")

    # ── Proxy Server ───────────────────────────────────────────────────────
    _proxy = ProxyServer(
        host=args.host,
        port=args.port,
        cache=_cache,
        filters=_filters,
        stats=_stats,
    )

    try:
        _proxy.start()  # Blocks until stopped
    except KeyboardInterrupt:
        _proxy.stop()
    except Exception as exc:
        log.critical(f"[Main] Fatal error: {exc}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
