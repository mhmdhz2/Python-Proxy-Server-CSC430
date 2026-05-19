"""
dashboard.py — Flask Admin Dashboard
======================================
[Hamza] Implemented the web-based admin panel.
        Routes: /logs, /cache, /stats, /blacklist, /whitelist

[Source: Flask documentation at https://flask.palletsprojects.com]
"""

from __future__ import annotations

import os
import threading
from typing import TYPE_CHECKING

from config import DASHBOARD_HOST, DASHBOARD_PORT, LOG_FILE

if TYPE_CHECKING:
    from cache import CacheManager
    from filters import FilterManager
    from utils import StatsTracker


def create_dashboard(cache, filters, stats):
    """
    [Hamza] Build and return a Flask app wired to the live proxy components.
    Call run_dashboard() to start it in a background thread.
    """
    try:
        from flask import Flask, jsonify, render_template_string, request as flask_request
    except ImportError:
        return None

    app = Flask(__name__)

    # ── HTML Template ─────────────────────────────────────────────────────
    BASE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Proxy Admin — {{ title }}</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: 'Segoe UI', Arial, sans-serif; background: #0d1117; color: #c9d1d9; }
  nav  { background: #161b22; border-bottom: 1px solid #30363d; padding: 12px 24px;
         display: flex; align-items: center; gap: 24px; }
  nav a { color: #58a6ff; text-decoration: none; font-size: 0.95rem; }
  nav a:hover { text-decoration: underline; }
  nav .brand { font-size: 1.1rem; font-weight: bold; color: #e6edf3; margin-right: auto; }
  main { max-width: 1100px; margin: 32px auto; padding: 0 24px; }
  h1 { font-size: 1.6rem; color: #e6edf3; margin-bottom: 20px; }
  .card { background: #161b22; border: 1px solid #30363d; border-radius: 8px;
          padding: 20px; margin-bottom: 20px; }
  .stat-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 16px; }
  .stat { background: #0d1117; border: 1px solid #30363d; border-radius: 6px;
          padding: 16px; text-align: center; }
  .stat .value { font-size: 2rem; font-weight: bold; color: #58a6ff; }
  .stat .label { font-size: 0.8rem; color: #8b949e; margin-top: 4px; }
  table { width: 100%; border-collapse: collapse; font-size: 0.88rem; }
  th, td { border: 1px solid #30363d; padding: 8px 12px; text-align: left; }
  th { background: #21262d; color: #8b949e; }
  tr:hover td { background: #161b22; }
  pre { background: #0d1117; border: 1px solid #30363d; border-radius: 6px;
        padding: 16px; overflow: auto; font-size: 0.8rem; max-height: 500px; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 0.75rem; }
  .hit  { background: #1a4731; color: #3fb950; }
  .miss { background: #3d1a1a; color: #f85149; }
  form { display: flex; gap: 8px; margin-top: 12px; }
  input[type=text] { background: #0d1117; border: 1px solid #30363d; color: #c9d1d9;
                     padding: 6px 12px; border-radius: 4px; flex: 1; }
  button { background: #238636; color: #fff; border: none; padding: 6px 14px;
           border-radius: 4px; cursor: pointer; }
  button.danger { background: #b62324; }
  .refresh { float: right; font-size: 0.8rem; color: #8b949e; margin-top: 4px; }
</style>
</head>
<body>
<nav>
  <span class="brand">🛡 Proxy Admin</span>
  <a href="/stats">Stats</a>
  <a href="/cache">Cache</a>
  <a href="/logs">Logs</a>
  <a href="/blacklist">Blacklist</a>
  <a href="/whitelist">Whitelist</a>
</nav>
<main>
  <h1>{{ title }}</h1>
  {% block content %}{% endblock %}
</main>
</body>
</html>"""

    STATS_HTML = BASE_HTML.replace(
        "{% block content %}{% endblock %}",
        """
<div class="card">
  <div class="stat-grid">
    <div class="stat"><div class="value">{{ s.total_requests }}</div><div class="label">Total Requests</div></div>
    <div class="stat"><div class="value">{{ s.active_connections }}</div><div class="label">Active Connections</div></div>
    <div class="stat"><div class="value" style="color:#3fb950">{{ cache_stats.hits }}</div><div class="label">Cache Hits</div></div>
    <div class="stat"><div class="value" style="color:#f85149">{{ cache_stats.misses }}</div><div class="label">Cache Misses</div></div>
    <div class="stat"><div class="value">{{ cache_stats.hit_rate }}</div><div class="label">Hit Rate</div></div>
    <div class="stat"><div class="value">{{ s.https_tunnels }}</div><div class="label">HTTPS Tunnels</div></div>
    <div class="stat"><div class="value" style="color:#f0883e">{{ s.error_count }}</div><div class="label">Errors</div></div>
    <div class="stat"><div class="value">{{ s.uptime }}</div><div class="label">Uptime</div></div>
  </div>
</div>
<div class="card">
  <h3 style="margin-bottom:12px;color:#e6edf3">Top Domains</h3>
  <table><tr><th>#</th><th>Domain</th><th>Requests</th></tr>
  {% for domain, count in s.top_domains %}
  <tr><td>{{ loop.index }}</td><td>{{ domain }}</td><td>{{ count }}</td></tr>
  {% endfor %}
  </table>
</div>""")

    CACHE_HTML = BASE_HTML.replace(
        "{% block content %}{% endblock %}",
        """
<div class="card">
  <p>Entries: <strong>{{ entries|length }}</strong> / {{ max_size }}
     &nbsp;&nbsp; Hit rate: <strong>{{ hit_rate }}</strong></p>
  <a href="/cache/clear" style="color:#f85149;font-size:0.85rem">Clear All Cache</a>
</div>
<div class="card">
  <table>
    <tr><th>URL</th><th>Age</th><th>TTL</th><th>Size</th><th>Status</th></tr>
    {% for e in entries %}
    <tr>
      <td style="word-break:break-all;max-width:400px">{{ e.url }}</td>
      <td>{{ e.age }}</td>
      <td>{{ e.ttl }}</td>
      <td>{{ e.size }}</td>
      <td><span class="badge {% if e.expired %}miss{% else %}hit{% endif %}">
          {{ 'Expired' if e.expired else 'Fresh' }}</span></td>
    </tr>
    {% endfor %}
  </table>
</div>""")

    LOGS_HTML = BASE_HTML.replace(
        "{% block content %}{% endblock %}",
        """<div class="card">
<span class="refresh">Auto-refresh every 5s</span>
<pre id="log">{{ log_content }}</pre>
</div>
<script>
setInterval(()=>fetch('/logs/raw').then(r=>r.text()).then(t=>{
  document.getElementById('log').textContent=t;
}),5000);
</script>""")

    LIST_HTML = BASE_HTML.replace(
        "{% block content %}{% endblock %}",
        """<div class="card">
  <p>{{ entries|length }} entries</p>
  {% if list_type == 'blacklist' %}
  <form method="POST" action="/blacklist/add">
    <input type="text" name="entry" placeholder="domain.com or IP"/>
    <button type="submit">Add to Blacklist</button>
  </form>
  {% endif %}
</div>
<div class="card">
  <table><tr><th>Entry</th>{% if list_type == 'blacklist' %}<th>Action</th>{% endif %}</tr>
  {% for e in entries %}
  <tr><td>{{ e }}</td>
  {% if list_type == 'blacklist' %}
  <td><a href="/blacklist/remove/{{ e }}" style="color:#f85149">Remove</a></td>
  {% endif %}
  </tr>
  {% endfor %}
  </table>
</div>""")

    # ── Routes ─────────────────────────────────────────────────────────────

    @app.route("/")
    @app.route("/stats")
    def route_stats():
        from flask import render_template_string
        return render_template_string(
            STATS_HTML,
            title="Statistics",
            s=stats.snapshot(),
            cache_stats=cache.stats,
        )

    @app.route("/cache")
    def route_cache():
        s = cache.stats
        return render_template_string(
            CACHE_HTML,
            title="Cache Entries",
            entries=cache.all_entries(),
            max_size=s["max_size"],
            hit_rate=s["hit_rate"],
        )

    @app.route("/cache/clear")
    def route_cache_clear():
        from flask import redirect
        cache.clear()
        return redirect("/cache")

    @app.route("/logs")
    def route_logs():
        content = _read_last_lines(LOG_FILE, 200)
        return render_template_string(LOGS_HTML, title="Proxy Logs", log_content=content)

    @app.route("/logs/raw")
    def route_logs_raw():
        from flask import Response
        return Response(_read_last_lines(LOG_FILE, 200), mimetype="text/plain")

    @app.route("/blacklist", methods=["GET"])
    def route_blacklist():
        return render_template_string(
            LIST_HTML, title="Blacklist", entries=filters.blacklist, list_type="blacklist"
        )

    @app.route("/blacklist/add", methods=["POST"])
    def route_blacklist_add():
        from flask import redirect
        entry = flask_request.form.get("entry", "").strip()
        if entry:
            filters.add_to_blacklist(entry)
        return redirect("/blacklist")

    @app.route("/blacklist/remove/<path:entry>")
    def route_blacklist_remove(entry):
        from flask import redirect
        filters.remove_from_blacklist(entry)
        return redirect("/blacklist")

    @app.route("/whitelist")
    def route_whitelist():
        return render_template_string(
            LIST_HTML, title="Whitelist", entries=filters.whitelist, list_type="whitelist"
        )

    # ── JSON API ──────────────────────────────────────────────────────────

    @app.route("/api/stats")
    def api_stats():
        return jsonify({**stats.snapshot(), "cache": cache.stats})

    return app


def _read_last_lines(path: str, n: int = 200) -> str:
    """[Hamza] Read the last *n* lines of a log file efficiently."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return "".join(lines[-n:])
    except FileNotFoundError:
        return "(No log file found yet)"
    except OSError as exc:
        return f"(Error reading log: {exc})"


def run_dashboard(cache, filters, stats) -> threading.Thread:
    """
    [Hamza] Start the Flask admin dashboard in a background daemon thread.
    Returns the thread object.
    """
    app = create_dashboard(cache, filters, stats)
    if app is None:
        return None

    def _run():
        import logging as _logging
        _logging.getLogger("werkzeug").setLevel(_logging.ERROR)
        app.run(
            host=DASHBOARD_HOST,
            port=DASHBOARD_PORT,
            debug=False,
            use_reloader=False,
            threaded=True,
        )

    t = threading.Thread(target=_run, daemon=True, name="DashboardThread")
    t.start()
    return t
