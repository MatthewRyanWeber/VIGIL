"""
Vigil Desktop — Windows native wrapper.

Runs the Flask server in a background thread and opens it in a native
WebView2 window instead of a browser tab. Uses HTTP internally since
traffic never leaves the machine.

Build:  build.bat
Run:    python vigil_desktop.py
"""

import sys, os, threading, atexit, socket

# Ensure the project root is on sys.path so core/ imports work
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.config import (
    APP_NAME, VERSION, CONFIG_FILE,
    load_config, save_config, default_config, ensure_workspaces,
    migrate_config_if_needed, log,
)
from core.routes import app, verify_ui_file, init_auth_cache
from core.checks import PollEngine
from core.status import load_status

PORT = 9443


def find_free_port(start=PORT):
    """Find a free port starting from the preferred one."""
    for p in range(start, start + 100):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", p))
            s.close()
            return p
        except OSError:
            continue
    return start


def setup_server(port):
    """Initialize config, auth, status, and poll engine."""
    if not CONFIG_FILE.exists():
        save_config(default_config())
    else:
        migrate_config_if_needed()

    ensure_workspaces()
    init_auth_cache()
    load_status()
    verify_ui_file()

    engine = PollEngine()
    engine.start()
    app.poll_engine = engine
    atexit.register(engine.stop)

    log.info("=" * 60)
    log.info(f"  {APP_NAME} {VERSION} — Desktop")
    log.info(f"  http://127.0.0.1:{port}")
    log.info(f"  Config: {CONFIG_FILE}")
    log.info("=" * 60)


def run_flask(port):
    """Run Flask in a thread (HTTP only — localhost traffic, no cert needed)."""
    app.run(host="127.0.0.1", port=port, debug=False,
            use_reloader=False, ssl_context=None)


def main():
    port = find_free_port()
    setup_server(port)

    flask_thread = threading.Thread(
        target=run_flask, args=(port,), daemon=True)
    flask_thread.start()

    url = f"http://127.0.0.1:{port}"
    try:
        import webview
        webview.create_window(
            f"{APP_NAME} {VERSION}",
            url,
            width=1440,
            height=900,
            min_size=(800, 500),
        )
        webview.start()
    except Exception as e:
        log.warning(f"Native window unavailable ({e}) — opening in browser")
        import webbrowser, time
        time.sleep(1.5)
        webbrowser.open(url)
        try:
            flask_thread.join()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
