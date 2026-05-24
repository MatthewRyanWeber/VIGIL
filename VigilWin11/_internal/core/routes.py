import json, time, uuid, functools
from datetime import datetime, timezone
from pathlib import Path
from flask import Flask, jsonify, request, Response

import threading as _thr

from core.config import (
    APP_NAME, VERSION, BASE_DIR, CONFIG_FILE, INDEX_HTML,
    DEFAULT_ROOM_INTERVAL, MIN_ROOM_INTERVAL, MAX_ROOM_INTERVAL,
    DEFAULT_TIMEOUT, MIN_TIMEOUT, MAX_TIMEOUT,
    DEFAULT_UDP_PORT, DEFAULT_SSH_PORT, VALID_CHECK_TYPES,
    MAX_WORKSPACES, MAX_ROOMS_PER_WS, MAX_DEVICES_PER_ROOM, MAX_NAME_LEN,
    DELETED_ROOM_RETENTION,
    load_config, save_config, config_transaction, TxAbort, backup_config,
    parse_and_migrate, validate_target, validate_port, log,
)
from core.status import get_status, public_status, get_all_public_status
from core.auth import (
    hash_pin, verify_pin, is_login_locked,
    record_login_failure, clear_login_failures,
    check_session, create_session,
    destroy_session, destroy_all_sessions, SESSION_EXPIRY,
)
from core.checks import clamp

# ─── Flask app ───────────────────────────────────────────────────────────────

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

app = Flask(__name__,
            static_folder=str(_PROJECT_ROOT / "static"),
            static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = 1 * 1024 * 1024
app.config["USING_HTTPS"] = False

# ─── Security headers ───────────────────────────────────────────────────────

@app.after_request
def _security_headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src https://fonts.gstatic.com; "
        "connect-src 'self'; "
        "img-src 'self' data:; "
        "frame-ancestors 'none'"
    )
    if app.config["USING_HTTPS"]:
        resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    if request.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp

# ─── Auth middleware & endpoints ─────────────────────────────────────────────

# Cached to avoid per-request disk I/O. Mutated by: init_auth_cache, api_auth_change_pin, api_auth_disable, api_import.
_auth_enabled: bool | None = None

def init_auth_cache():
    global _auth_enabled
    config = load_config()
    _auth_enabled = bool(config.get("pin_hash"))

@app.before_request
def _require_auth():
    if not request.path.startswith("/api/"):
        return
    if request.path.startswith("/api/auth/"):
        return
    if not _auth_enabled:
        return
    token = request.cookies.get("vigil_session")
    if not token or not check_session(token):
        return jsonify({"error": "Not authenticated."}), 401

@app.route("/api/auth/check", methods=["GET"])
def api_auth_check():
    config = load_config()
    has_pin = bool(config.get("pin_hash"))
    if not has_pin:
        return jsonify({"authenticated": True, "auth_enabled": False})
    token = request.cookies.get("vigil_session")
    if token and check_session(token):
        return jsonify({"authenticated": True, "auth_enabled": True})
    return jsonify({"authenticated": False, "auth_enabled": True}), 401

@app.route("/api/auth/login", methods=["POST"])
def api_auth_login():
    client_ip = request.remote_addr or "unknown"
    locked, remaining = is_login_locked(client_ip)
    if locked:
        return jsonify({"error": f"Too many attempts. Try again in {remaining}s."}), 429
    data = request.get_json(silent=True) or {}
    pin = data.get("pin", "")
    config = load_config()
    stored = config.get("pin_hash")
    if not stored:
        return jsonify({"error": "No PIN configured."}), 500
    if not verify_pin(pin, stored):
        record_login_failure(client_ip)
        log.warning(f"Failed login attempt from {client_ip}.")
        return jsonify({"error": "Invalid PIN."}), 401
    clear_login_failures(client_ip)
    if not stored.startswith("pbkdf2:"):
        config["pin_hash"] = hash_pin(pin)
        save_config(config)
        log.info("Migrated PIN hash from SHA-256 to PBKDF2.")
    token = create_session()
    resp = jsonify({"ok": True})
    resp.set_cookie("vigil_session", token, httponly=True,
                    samesite="Strict", secure=app.config["USING_HTTPS"],
                    max_age=SESSION_EXPIRY)
    log.info("Session created — user authenticated.")
    return resp

@app.route("/api/auth/logout", methods=["POST"])
def api_auth_logout():
    token = request.cookies.get("vigil_session")
    if token:
        destroy_session(token)
    resp = jsonify({"ok": True})
    resp.delete_cookie("vigil_session")
    return resp

@app.route("/api/auth/change-pin", methods=["POST"])
def api_auth_change_pin():
    data = request.get_json(silent=True) or {}
    current = data.get("current_pin", "")
    new_pin = data.get("new_pin", "").strip()
    if not new_pin or len(new_pin) < 4:
        return jsonify({"error": "New PIN must be at least 4 characters."}), 400
    config = load_config()
    stored = config.get("pin_hash")
    if stored and not verify_pin(current, stored):
        return jsonify({"error": "Current PIN is incorrect."}), 401
    config["pin_hash"] = hash_pin(new_pin)
    save_config(config)
    destroy_all_sessions()
    global _auth_enabled
    _auth_enabled = True
    log.info("PIN changed — all sessions invalidated.")
    return jsonify({"ok": True})

@app.route("/api/auth/disable", methods=["POST"])
def api_auth_disable():
    data = request.get_json(silent=True) or {}
    current = data.get("pin", "")
    config = load_config()
    stored = config.get("pin_hash")
    if stored and not verify_pin(current, stored):
        return jsonify({"error": "Incorrect PIN."}), 401
    config.pop("pin_hash", None)
    save_config(config)
    destroy_all_sessions()
    global _auth_enabled
    _auth_enabled = False
    log.info("PIN protection disabled.")
    return jsonify({"ok": True})

# ─── Helpers ─────────────────────────────────────────────────────────────────

def with_transaction(fn):
    """Decorator: wraps a route in config_transaction(). The decorated function
    receives config as its first arg. Raise TxAbort to return an error response."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            with config_transaction() as config:
                return fn(config, *args, **kwargs)
        except TxAbort as e:
            return jsonify({"error": e.message}), e.status
    return wrapper

def _find_workspace(config, workspace_id):
    return next((w for w in config.get("workspaces", []) if w["id"] == workspace_id), None)

def _find_room(workspace, room_id):
    return next((r for r in workspace.get("rooms", []) if r["id"] == room_id), None)

def _find_room_anywhere(config, room_id):
    for ws in config.get("workspaces", []):
        room = _find_room(ws, room_id)
        if room:
            return ws, room
    return None, None

def _purge_expired_deletions(config):
    deleted = config.get("_deleted_rooms", [])
    if not deleted:
        return
    cutoff = datetime.now(timezone.utc).timestamp() - DELETED_ROOM_RETENTION
    config["_deleted_rooms"] = [
        e for e in deleted
        if datetime.fromisoformat(e["deleted_at"]).timestamp() > cutoff
    ]

def _find_device(room, device_id):
    return next((d for d in room.get("devices", []) if d["id"] == device_id), None)

def _room_with_status(room):
    r = dict(room)
    r["devices"] = [dict(d) | public_status(d["id"]) for d in room.get("devices", [])]
    engine = _poll_engine()
    if engine:
        r["poll_status"] = engine.room_poll_info(room["id"])
    return r

def _count_devices(config):
    total = 0
    for ws in config.get("workspaces", []):
        for room in ws.get("rooms", []):
            total += len(room.get("devices", []))
    return total

def _count_rooms(config):
    return sum(len(ws.get("rooms", [])) for ws in config.get("workspaces", []))

def _poll_engine():
    return getattr(app, 'poll_engine', None)

def _sanitize_imported_config(data):
    """Strip invalid devices/rooms from imported config to prevent runtime errors."""
    for ws in data.get("workspaces", []):
        if not isinstance(ws.get("rooms"), list):
            ws["rooms"] = []
            continue
        clean_rooms = []
        for room in ws["rooms"]:
            if not isinstance(room, dict) or not room.get("id") or not room.get("name"):
                continue
            devs = room.get("devices")
            if not isinstance(devs, list):
                room["devices"] = []
            else:
                room["devices"] = [
                    d for d in devs
                    if isinstance(d, dict) and d.get("id") and d.get("name") and d.get("ip")
                ]
            clean_rooms.append(room)
        ws["rooms"] = clean_rooms

# ─── Frontend ────────────────────────────────────────────────────────────────

@app.route("/")
@app.route("/w/<workspace_id>")
def index(workspace_id=None):
    html = None
    try:
        if INDEX_HTML.exists():
            html = INDEX_HTML.read_text(encoding="utf-8")
            if len(html) < 100:
                log.error(f"index.html is suspiciously short ({len(html)} bytes) — file may be corrupted or mid-sync")
                html = None
        else:
            log.error(f"index.html not found at: {INDEX_HTML}")
    except Exception as e:
        log.error(f"Failed reading index.html: {e}")
    if not html:
        html = (
            "<!DOCTYPE html><html><body style='font-family:sans-serif;padding:40px;background:#1a1d21;color:#e8eaed'>"
            "<h1 style='color:#ef4444'>Vigil: UI missing</h1>"
            "<p>Cannot find or read <code>index.html</code>.</p>"
            "<p>Ensure <code>index.html</code> sits next to <code>vigil.py</code> "
            "and isn't locked by another process.</p>"
            "</body></html>"
        )
    return Response(html, mimetype="text/html; charset=utf-8")

def verify_ui_file() -> None:
    if INDEX_HTML.exists():
        try:
            size = INDEX_HTML.stat().st_size
            if size >= 10_000:
                log.info(f"UI OK: {size:,} bytes at {INDEX_HTML}")
                return
            log.error(f"index.html is only {size} bytes — likely corrupt or mid-sync")
        except Exception:
            pass
    else:
        log.error(f"index.html not found at: {INDEX_HTML}")
    log.error("Vigil will serve an error page until index.html is restored.")

# ─── Workspace endpoints ─────────────────────────────────────────────────────

@app.route("/api/workspaces", methods=["GET"])
def api_workspaces():
    config = load_config()
    out = []
    for ws in config.get("workspaces", []):
        out.append({
            "id":    ws["id"],
            "name":  ws.get("name", "Untitled"),
            "rooms": [_room_with_status(r) for r in ws.get("rooms", [])],
        })
    return jsonify(out)

@app.route("/api/workspaces/<workspace_id>", methods=["GET"])
def api_workspace_get(workspace_id):
    config = load_config()
    ws = _find_workspace(config, workspace_id)
    if not ws:
        return jsonify({"error": "Workspace not found."}), 404
    return jsonify({
        "id":    ws["id"],
        "name":  ws.get("name", "Untitled"),
        "rooms": [_room_with_status(r) for r in ws.get("rooms", [])],
    })

@app.route("/api/workspaces", methods=["POST"])
@with_transaction
def api_workspace_create(config):
    data = request.get_json(silent=True) or {}
    name = data.get("name", "").strip()
    if not name:
        raise TxAbort("Workspace name is required.", 400)
    if len(config.get("workspaces", [])) >= MAX_WORKSPACES:
        raise TxAbort(f"Maximum {MAX_WORKSPACES} workspaces allowed.", 400)
    ws = {"id": str(uuid.uuid4()), "name": name, "rooms": []}
    config["workspaces"].append(ws)
    log.info(f"Workspace created: {name}")
    return jsonify(ws), 201

@app.route("/api/workspaces/<workspace_id>", methods=["PUT"])
@with_transaction
def api_workspace_update(config, workspace_id):
    data = request.get_json(silent=True) or {}
    if "name" in data:
        name = data["name"].strip()
        if not name:
            raise TxAbort("Workspace name is required.", 400)
        if len(name) > MAX_NAME_LEN:
            raise TxAbort(f"Name must be under {MAX_NAME_LEN} characters.", 400)
    ws = _find_workspace(config, workspace_id)
    if not ws:
        raise TxAbort("Workspace not found.", 404)
    if "name" in data:
        ws["name"] = data["name"].strip()
    return jsonify(ws)

@app.route("/api/workspaces/<workspace_id>", methods=["DELETE"])
def api_workspace_delete(workspace_id):
    return jsonify({"error": "Workspaces cannot be deleted."}), 400

# ─── Room endpoints ──────────────────────────────────────────────────────────

@app.route("/api/rooms", methods=["GET"])
def api_rooms():
    """Legacy: list all rooms flat. Prefer /api/workspaces for new code."""
    config = load_config()
    all_rooms = []
    for ws in config.get("workspaces", []):
        for room in ws.get("rooms", []):
            r = _room_with_status(room)
            r["workspace_id"]   = ws["id"]
            r["workspace_name"] = ws.get("name", "")
            all_rooms.append(r)
    return jsonify(all_rooms)

@app.route("/api/workspaces/<workspace_id>/rooms", methods=["POST"])
@with_transaction
def api_create_room(config, workspace_id):
    data     = request.get_json(silent=True) or {}
    name     = data.get("name", "").strip()
    interval = clamp(data.get("interval", DEFAULT_ROOM_INTERVAL),
                     MIN_ROOM_INTERVAL, MAX_ROOM_INTERVAL, DEFAULT_ROOM_INTERVAL)
    if not name:
        raise TxAbort("Room name is required.", 400)
    if len(name) > MAX_NAME_LEN:
        raise TxAbort(f"Name must be under {MAX_NAME_LEN} characters.", 400)
    ws = _find_workspace(config, workspace_id)
    if not ws:
        raise TxAbort("Workspace not found.", 404)
    if len(ws.get("rooms", [])) >= MAX_ROOMS_PER_WS:
        raise TxAbort(f"Maximum {MAX_ROOMS_PER_WS} rooms per workspace.", 400)
    room = {"id": str(uuid.uuid4()), "name": name,
            "interval": interval, "devices": []}
    ws.setdefault("rooms", []).append(room)
    log.info(f"Room created in workspace {ws['name']}: {name}")
    engine = _poll_engine()
    if engine:
        engine.poll_now(room["id"])
    return jsonify(room), 201

@app.route("/api/rooms/<room_id>", methods=["PUT"])
@with_transaction
def api_update_room(config, room_id):
    data = request.get_json(silent=True) or {}
    if "name" in data:
        name = data["name"].strip()
        if not name:
            raise TxAbort("Room name is required.", 400)
        if len(name) > MAX_NAME_LEN:
            raise TxAbort(f"Name must be under {MAX_NAME_LEN} characters.", 400)
    _, room = _find_room_anywhere(config, room_id)
    if not room:
        raise TxAbort("Room not found.", 404)
    if "name" in data:
        room["name"] = data["name"].strip()
    if "interval" in data:
        room["interval"] = clamp(data["interval"], MIN_ROOM_INTERVAL,
                                 MAX_ROOM_INTERVAL, DEFAULT_ROOM_INTERVAL)
    result = _room_with_status(room)
    engine = _poll_engine()
    if engine:
        engine.poll_now(room["id"])
    return jsonify(result)

@app.route("/api/rooms/<room_id>", methods=["DELETE"])
@with_transaction
def api_delete_room(config, room_id):
    ws, room = _find_room_anywhere(config, room_id)
    if not room:
        raise TxAbort("Room not found.", 404)
    ws["rooms"] = [r for r in ws["rooms"] if r["id"] != room_id]
    deleted = config.setdefault("_deleted_rooms", [])
    deleted.append({
        "room": room,
        "workspace_id": ws["id"],
        "deleted_at": datetime.now(timezone.utc).isoformat(),
    })
    _purge_expired_deletions(config)
    log.info(f"Room soft-deleted: {room['name']}")
    return jsonify({"ok": True})

@app.route("/api/deleted-rooms", methods=["GET"])
def api_list_deleted_rooms():
    config = load_config()
    _purge_expired_deletions(config)
    entries = config.get("_deleted_rooms", [])
    return jsonify([{
        "id":           e["room"]["id"],
        "name":         e["room"].get("name", ""),
        "device_count": len(e["room"].get("devices", [])),
        "workspace_id": e["workspace_id"],
        "deleted_at":   e["deleted_at"],
    } for e in entries])

@app.route("/api/deleted-rooms/<room_id>/restore", methods=["POST"])
@with_transaction
def api_restore_room(config, room_id):
    _purge_expired_deletions(config)
    deleted = config.get("_deleted_rooms", [])
    entry = next((e for e in deleted if e["room"]["id"] == room_id), None)
    if not entry:
        raise TxAbort("Deleted room not found.", 404)
    ws = _find_workspace(config, entry["workspace_id"])
    if not ws:
        ws = config["workspaces"][0]
    if len(ws.get("rooms", [])) >= MAX_ROOMS_PER_WS:
        raise TxAbort("Workspace is full.", 400)
    ws["rooms"].append(entry["room"])
    config["_deleted_rooms"] = [e for e in deleted if e["room"]["id"] != room_id]
    log.info(f"Room restored: {entry['room']['name']}")
    return jsonify({"ok": True})

@app.route("/api/deleted-rooms/<room_id>", methods=["DELETE"])
@with_transaction
def api_purge_room(config, room_id):
    deleted = config.get("_deleted_rooms", [])
    before = len(deleted)
    config["_deleted_rooms"] = [e for e in deleted if e["room"]["id"] != room_id]
    if len(config["_deleted_rooms"]) == before:
        raise TxAbort("Deleted room not found.", 404)
    log.info(f"Room permanently deleted: {room_id}")
    return jsonify({"ok": True})

@app.route("/api/workspaces/<wsid>/rooms/order", methods=["PUT"])
@with_transaction
def api_reorder_rooms(config, wsid):
    data  = request.get_json(silent=True) or {}
    order = data.get("order")
    if not isinstance(order, list) or not all(isinstance(x, str) for x in order):
        raise TxAbort("order must be a list of room ids.", 400)
    ws = _find_workspace(config, wsid)
    if not ws:
        raise TxAbort("Workspace not found.", 404)
    existing = {r["id"]: r for r in ws.get("rooms", [])}
    if set(order) != set(existing.keys()):
        raise TxAbort("order must list exactly the rooms in this workspace.", 400)
    ws["rooms"] = [existing[rid] for rid in order]
    log.info(f"Rooms reordered in workspace {ws.get('name', wsid)}")
    return jsonify({"ok": True})

@app.route("/api/rooms/<room_id>/layout", methods=["PUT"])
@with_transaction
def api_update_room_layout(config, room_id):
    data = request.get_json(silent=True) or {}
    w = data.get("w")
    h = data.get("h")
    if w is not None and h is not None:
        try:
            w, h = int(w), int(h)
        except (TypeError, ValueError):
            raise TxAbort("w and h must be integers.", 400)
    _, room = _find_room_anywhere(config, room_id)
    if not room:
        raise TxAbort("Room not found.", 404)
    if w is None or h is None or w <= 0 or h <= 0:
        room.pop("layout", None)
    else:
        room["layout"] = {"w": w, "h": h}
    return jsonify({"ok": True})

# ─── Device endpoints ────────────────────────────────────────────────────────

@app.route("/api/rooms/<room_id>/devices", methods=["POST"])
@with_transaction
def api_create_device(config, room_id):
    data       = request.get_json(silent=True) or {}
    name       = data.get("name", "").strip()
    ip         = data.get("ip", "").strip()
    check_type = data.get("check_type", "ping")
    timeout    = clamp(data.get("timeout", DEFAULT_TIMEOUT),
                        MIN_TIMEOUT, MAX_TIMEOUT, DEFAULT_TIMEOUT)
    udp_port = validate_port(data.get("udp_port", DEFAULT_UDP_PORT))
    if udp_port is None:
        raise TxAbort("udp_port must be 1-65535.", 400)
    ssh_port = validate_port(data.get("ssh_port", DEFAULT_SSH_PORT))
    if ssh_port is None:
        raise TxAbort("ssh_port must be 1-65535.", 400)
    http_port = validate_port(data.get("http_port", 80))
    if http_port is None:
        raise TxAbort("http_port must be 1-65535.", 400)
    if not name:
        raise TxAbort("Device name is required.", 400)
    if len(name) > MAX_NAME_LEN:
        raise TxAbort(f"Name must be under {MAX_NAME_LEN} characters.", 400)
    if not ip:
        raise TxAbort("Address is required.", 400)
    validated = validate_target(ip)
    if not validated:
        raise TxAbort("Invalid address. Enter a valid IP or hostname.", 400)
    ip = validated
    if check_type not in VALID_CHECK_TYPES:
        raise TxAbort(f"check_type must be one of: {', '.join(VALID_CHECK_TYPES)}.", 400)
    _, room = _find_room_anywhere(config, room_id)
    if not room:
        raise TxAbort("Room not found.", 404)
    if len(room.get("devices", [])) >= MAX_DEVICES_PER_ROOM:
        raise TxAbort(f"Maximum {MAX_DEVICES_PER_ROOM} devices per room.", 400)
    device = {"id": str(uuid.uuid4()), "name": name, "ip": ip,
              "check_type": check_type, "udp_port": udp_port, "ssh_port": ssh_port,
              "http_port": http_port, "timeout": timeout}
    room.setdefault("devices", []).append(device)
    log.info(f"Device added: {name} ({ip})")
    engine = _poll_engine()
    if engine:
        engine.poll_now(room["id"])
    return jsonify(dict(device) | public_status(device["id"])), 201

@app.route("/api/rooms/<room_id>/devices/<device_id>", methods=["PUT"])
@with_transaction
def api_update_device(config, room_id, device_id):
    data = request.get_json(silent=True) or {}
    updates = {}
    if "name" in data:
        n = data["name"].strip()
        if not n:
            raise TxAbort("Device name is required.", 400)
        if len(n) > MAX_NAME_LEN:
            raise TxAbort(f"Name must be under {MAX_NAME_LEN} characters.", 400)
        updates["name"] = n
    if "ip" in data:
        validated = validate_target(data["ip"].strip())
        if not validated:
            raise TxAbort("Invalid address.", 400)
        updates["ip"] = validated
    if "timeout" in data:
        updates["timeout"] = clamp(data["timeout"], MIN_TIMEOUT, MAX_TIMEOUT, DEFAULT_TIMEOUT)
    if "check_type" in data:
        ct = data["check_type"]
        if ct not in VALID_CHECK_TYPES:
            raise TxAbort(f"check_type must be one of: {', '.join(VALID_CHECK_TYPES)}.", 400)
        updates["check_type"] = ct
    for port_key in ("ssh_port", "udp_port", "http_port"):
        if port_key in data:
            p = validate_port(data[port_key])
            if p is None:
                raise TxAbort(f"{port_key} must be 1-65535.", 400)
            updates[port_key] = p
    _, room = _find_room_anywhere(config, room_id)
    if not room:
        raise TxAbort("Room not found.", 404)
    device = _find_device(room, device_id)
    if not device:
        raise TxAbort("Device not found.", 404)
    device.update(updates)
    log.info(f"Device updated: {device['name']}")
    engine = _poll_engine()
    if engine:
        engine.poll_now(room["id"])
    return jsonify(dict(device) | public_status(device["id"]))

@app.route("/api/rooms/<room_id>/devices/<device_id>", methods=["DELETE"])
@with_transaction
def api_delete_device(config, room_id, device_id):
    _, room = _find_room_anywhere(config, room_id)
    if not room:
        raise TxAbort("Room not found.", 404)
    device = _find_device(room, device_id)
    if not device:
        raise TxAbort("Device not found.", 404)
    room["devices"] = [d for d in room["devices"] if d["id"] != device_id]
    log.info(f"Device deleted: {device['name']}")
    return jsonify({"ok": True})

# ─── Status & control ────────────────────────────────────────────────────────

@app.route("/api/status", methods=["GET"])
def api_status():
    return jsonify(get_all_public_status())

_poll_cooldowns: dict[str, float] = {}
_poll_cooldowns_lock = _thr.Lock()
_POLL_COOLDOWN = 10

@app.route("/api/poll", methods=["POST"])
def api_poll():
    room_id = (request.get_json(silent=True) or {}).get("room_id")
    key = request.cookies.get("vigil_session") or request.remote_addr or "anon"
    if room_id:
        key += ":" + room_id
    now = time.time()
    with _poll_cooldowns_lock:
        stale = [k for k, v in _poll_cooldowns.items() if now - v > _POLL_COOLDOWN]
        for k in stale:
            del _poll_cooldowns[k]
        last = _poll_cooldowns.get(key, 0.0)
        if now - last < _POLL_COOLDOWN:
            remaining = int(_POLL_COOLDOWN - (now - last))
            return jsonify({"error": f"Poll cooldown — try again in {remaining}s."}), 429
        _poll_cooldowns[key] = now
    engine = _poll_engine()
    if engine:
        engine.poll_now(room_id)
    return jsonify({"ok": True})

# ─── Config endpoints ────────────────────────────────────────────────────────

@app.route("/api/config/export", methods=["GET"])
def api_export():
    config = load_config()
    config.pop("pin_hash", None)
    return Response(
        json.dumps(config, indent=2),
        mimetype="application/json",
        headers={"Content-Disposition": "attachment; filename=vigil_config.json"}
    )

@app.route("/api/config/import", methods=["POST"])
def api_import():
    data = request.get_json(silent=True)
    if not data or not isinstance(data, dict):
        return jsonify({"error": "Invalid JSON. Expected a JSON object with a 'workspaces' key."}), 400
    data.pop("pin_hash", None)
    backup_file = backup_config()
    if "workspaces" in data:
        if not isinstance(data["workspaces"], list):
            return jsonify({"error": "Invalid config — 'workspaces' must be an array."}), 400
    elif "rooms" in data:
        if not isinstance(data["rooms"], list):
            return jsonify({"error": "Invalid config — 'rooms' must be an array."}), 400
    else:
        return jsonify({"error": "Invalid config — must contain a 'workspaces' or 'rooms' key."}), 400
    data = parse_and_migrate(data)
    _sanitize_imported_config(data)
    with config_transaction() as config:
        pin = config.get("pin_hash")
        config.clear()
        config.update(data)
        if pin:
            config["pin_hash"] = pin
    init_auth_cache()
    log.info("Config imported via API.")
    engine = _poll_engine()
    if engine:
        engine.poll_now()
    return jsonify({"ok": True, "backup": backup_file})

@app.route("/api/config/save", methods=["POST"])
def api_save():
    config = load_config()
    save_config(config)
    mtime = CONFIG_FILE.stat().st_mtime if CONFIG_FILE.exists() else None
    saved_at = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat() if mtime else None
    return jsonify({"ok": True, "saved_at": saved_at})

@app.route("/api/config/info", methods=["GET"])
def api_config_info():
    exists = CONFIG_FILE.exists()
    mtime  = CONFIG_FILE.stat().st_mtime if exists else None
    saved_at = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat() if mtime else None
    config   = load_config()
    return jsonify({
        "saved_at":        saved_at,
        "workspace_count": len(config.get("workspaces", [])),
        "room_count":      _count_rooms(config),
        "device_count":    _count_devices(config),
    })
