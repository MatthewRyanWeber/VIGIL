# =============================================================================
#  Vigil — Network Device Monitor
# =============================================================================
#
#  VERSION:  v2.0
#
#  USAGE:
#      python vigil.py                 # HTTPS on https://127.0.0.1:9443
#      python vigil.py --no-https      # Plain HTTP (not recommended)
#      python vigil.py --port 9000     # Custom port
#      python vigil.py --host 0.0.0.0  # Allow LAN access
#      python vigil.py --debug         # Flask debug mode
#
#  SECURITY:
#      PIN protection is off by default. Enable it in Settings > Security.
#      When enabled, all API endpoints require a valid session. Sessions
#      expire after 8 hours of inactivity or when the browser is closed.
#
#  EXE BUILD (PyInstaller):
#      pip install pyinstaller
#      pyinstaller --onefile --name Vigil ^
#          --add-data "index.html;." ^
#          --add-data "core;core" ^
#          --add-data "static;static" ^
#          vigil.py
#      The resulting Vigil.exe will be in the dist/ folder.
#
# =============================================================================

import sys, argparse, threading, atexit

from core.config import (
    APP_NAME, VERSION, BASE_DIR, CONFIG_FILE,
    load_config, save_config, default_config, ensure_workspaces,
    migrate_config_if_needed, log,
)
from core.routes import app, verify_ui_file, init_auth_cache
from core.checks import PollEngine
from core.status import load_status
from core.certs import setup_https_cert, launch_browser


def parse_args():
    p = argparse.ArgumentParser(description=f"{APP_NAME} {VERSION}")
    p.add_argument("--host",     default="127.0.0.1")
    p.add_argument("--port",     default=9443,  type=int)
    p.add_argument("--no-https",    dest="no_https",   action="store_true")
    p.add_argument("--no-browser",  dest="no_browser", action="store_true",
                   help="Don't automatically open the browser on startup")
    p.add_argument("--debug",       action="store_true")
    return p.parse_args()


def _check_port_available(host: str, port: int) -> None:
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind((host, port))
    except OSError as e:
        log.error(f"Port {port} is in use: {e}")
        log.error(f"Try: python vigil.py --port {port + 1}")
        sys.exit(1)
    finally:
        s.close()


def main():
    args = parse_args()
    _check_port_available(args.host, args.port)

    # ── First-run setup ──────────────────────────────────────────────────
    first_run = not CONFIG_FILE.exists()
    if first_run:
        save_config(default_config())
    else:
        migrate_config_if_needed()

    # ── Startup banner ───────────────────────────────────────────────────
    config = load_config()
    proto = "http" if args.no_https else "https"
    log.info("=" * 60)
    log.info(f"  {APP_NAME} {VERSION}")
    log.info(f"  {proto}://{args.host}:{args.port}")
    log.info(f"  Config: {CONFIG_FILE}")
    if config.get("pin_hash"):
        log.info("  PIN protection: ON")
    else:
        log.info("  PIN protection: OFF (enable in Settings > Security)")
    log.info("=" * 60)

    # ── Security warnings ────────────────────────────────────────────────
    if args.debug and args.host != "127.0.0.1":
        log.critical("DANGER: --debug on a non-loopback interface exposes the "
                     "Werkzeug debugger to the network. Anyone can execute code.")
        log.critical("Refusing to start. Remove --debug or use --host 127.0.0.1.")
        sys.exit(1)
    if args.host != "127.0.0.1" and not config.get("pin_hash"):
        log.warning("WARNING: Binding to %s with no PIN — anyone on this "
                    "network can access Vigil. Enable a PIN in Settings > Security.", args.host)

    # ── HTTPS ────────────────────────────────────────────────────────────
    ssl_ctx = None
    if not args.no_https:
        try:
            ssl_ctx = setup_https_cert()
            app.config["USING_HTTPS"] = True
            log.info("HTTPS enabled — browser will open automatically.")
        except Exception as e:
            log.warning(f"HTTPS failed ({e}) — using HTTP")
            log.info("HTTP mode — browser will open automatically.")
    else:
        log.info("HTTP mode — browser will open automatically.")

    # ── Ensure all workspaces exist ─────────────────────────────────────
    ensure_workspaces()

    # ── Cache auth state ────────────────────────────────────────────────
    init_auth_cache()

    # ── Restore device status from last run ─────────────────────────────
    load_status()

    # ── Start poll engine ────────────────────────────────────────────────
    app.poll_engine = PollEngine()
    app.poll_engine.start()
    atexit.register(app.poll_engine.stop)

    # ── Ensure the UI exists on disk ─────────────────────────────────────
    verify_ui_file()

    # ── Auto-launch browser ──────────────────────────────────────────────
    if not args.no_browser:
        url = f"{'http' if args.no_https else 'https'}://127.0.0.1:{args.port}"
        threading.Timer(1.5, launch_browser, args=(url, not args.no_https)).start()

    # ── Flask ────────────────────────────────────────────────────────────
    app.run(host=args.host, port=args.port, debug=args.debug,
            use_reloader=False, ssl_context=ssl_ctx)

if __name__ == "__main__":
    main()
