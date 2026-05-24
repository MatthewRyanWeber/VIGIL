import sys, json, time, uuid, re, copy, ipaddress, logging, threading, shutil
from contextlib import contextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path

# ─── Base directory (handles PyInstaller frozen builds) ──────────────────────

if getattr(sys, 'frozen', False):
    BASE_DIR    = Path(sys.executable).parent
    BUNDLE_DIR  = Path(sys._MEIPASS)
else:
    BASE_DIR    = Path(__file__).resolve().parent.parent
    BUNDLE_DIR  = BASE_DIR

# ─── Constants ───────────────────────────────────────────────────────────────

APP_NAME    = "Vigil"
VERSION     = "v2.0"
CONFIG_FILE = BASE_DIR / "config.json"
LOG_FILE    = BASE_DIR / "vigil.log"
INDEX_HTML  = BUNDLE_DIR / "index.html"
STATUS_FILE = BASE_DIR / "status.json"

DEFAULT_ROOM_INTERVAL = 300
MIN_ROOM_INTERVAL     = 300
MAX_ROOM_INTERVAL     = 7200
DEFAULT_TIMEOUT       = 2.0
MIN_TIMEOUT           = 0.5
MAX_TIMEOUT           = 30.0
DEFAULT_UDP_PORT      = 9
DEFAULT_SSH_PORT      = 22
DEFAULT_PING_TIMEOUT  = 2
VALID_CHECK_TYPES     = ("ping", "udp", "ssh", "http")

MAX_WORKSPACES       = 8
DEFAULT_WORKSPACES   = 4
MAX_ROOMS_PER_WS     = 50
MAX_DEVICES_PER_ROOM = 4
MAX_NAME_LEN         = 100
MAX_IP_LEN           = 45
# Tally-mark convention from broadcast/NOC environments, not Roman numerals
WS_DEFAULT_NAMES     = ["I", "II", "III", "IIII", "IIIII", "IIIIII", "IIIIIII", "IIIIIIII"]

DELETED_ROOM_RETENTION = 86400

LOGIN_MAX_FAILURES = 5
LOGIN_LOCKOUT_SECS = 60
PBKDF2_ITERATIONS  = 600_000

# ─── Input validation ──────────────────────────────────────────────────────

_HOSTNAME_RE = re.compile(
    r'^(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.[A-Za-z0-9-]{1,63})*\.?$'
)

def validate_target(target: str) -> str | None:
    """Validate an IP address or hostname. Returns the canonical form, or None."""
    try:
        return str(ipaddress.ip_address(target))
    except ValueError:
        pass
    if _HOSTNAME_RE.match(target) and len(target) <= 253:
        return target.rstrip('.')
    return None

def validate_port(value) -> int | None:
    """Validate a port number (1-65535). Returns the int, or None on failure."""
    try:
        p = int(value)
        if p < 1 or p > 65535:
            return None
        return p
    except (TypeError, ValueError):
        return None

# ─── Logging (rotating file handler, 5 MB x 3 backups) ──────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024,
                            backupCount=3, encoding="utf-8"),
    ],
)
log = logging.getLogger(APP_NAME)

# ─── Config file I/O ────────────────────────────────────────────────────────

_config_lock = threading.RLock()
_cached_config: dict | None = None
_cached_mtime: float = 0.0

def default_config() -> dict:
    return {"workspaces": [{"id": str(uuid.uuid4()), "name": "Main", "rooms": []}]}

def parse_and_migrate(data: dict) -> dict:
    """Normalize config structure. Does NOT save — caller is responsible."""
    if "workspaces" not in data:
        legacy_rooms = data.get("rooms", [])
        data["workspaces"] = [{
            "id":    str(uuid.uuid4()),
            "name":  "Main",
            "rooms": legacy_rooms,
        }]
        data.pop("rooms", None)
    if not data.get("workspaces"):
        data["workspaces"] = [{"id": str(uuid.uuid4()), "name": "Main", "rooms": []}]
    for ws in data["workspaces"]:
        if "rooms" not in ws:
            ws["rooms"] = []
    return data

def load_config() -> dict:
    global _cached_config, _cached_mtime
    last_err = None
    for attempt in range(5):
        with _config_lock:
            if not CONFIG_FILE.exists():
                return default_config()
            try:
                mtime = CONFIG_FILE.stat().st_mtime
                if _cached_config is not None and mtime == _cached_mtime:
                    return copy.deepcopy(_cached_config)
                raw = CONFIG_FILE.read_text(encoding="utf-8")
                data = json.loads(raw)
                data = parse_and_migrate(data)
                _cached_config = data
                _cached_mtime = mtime
                return copy.deepcopy(data)
            except (PermissionError, OSError) as e:
                last_err = e
            except Exception as e:
                log.error(f"Config load failed: {e} — using defaults")
                return default_config()
        if attempt < 4:
            time.sleep(0.05 * (attempt + 1))
    log.error(f"Config file locked after 5 retries: {last_err}")
    return default_config()

def migrate_config_if_needed():
    """Run once at startup to migrate legacy configs and persist the result."""
    if not CONFIG_FILE.exists():
        return
    config = load_config()
    save_config(config)
    log.info("Config migration check complete.")

def save_config(data: dict) -> None:
    global _cached_config, _cached_mtime
    tmp = CONFIG_FILE.with_suffix('.tmp')
    last_err = None
    for attempt in range(5):
        with _config_lock:
            try:
                tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
                tmp.replace(CONFIG_FILE)
                _cached_config = copy.deepcopy(data)
                _cached_mtime = CONFIG_FILE.stat().st_mtime
                log.info("Config saved.")
                return
            except (PermissionError, OSError) as e:
                last_err = e
            except Exception as e:
                log.error(f"Config save failed: {e}")
                break
        if attempt < 4:
            time.sleep(0.05 * (attempt + 1))
    if last_err:
        log.error(f"Config save failed after retries: {last_err}")
    try: tmp.unlink(missing_ok=True)
    except Exception: pass

class SkipSave(Exception):
    """Raised inside config_transaction to abort without writing."""
    pass

class TxAbort(Exception):
    """Raised inside a @with_transaction route to abort and return an HTTP error."""
    def __init__(self, message, status=400):
        self.message = message
        self.status = status

@contextmanager
def config_transaction():
    """Atomic read-modify-write. Holds the lock for the full operation,
    preventing TOCTOU races between concurrent requests.
    Raise SkipSave inside the block to exit without writing."""
    with _config_lock:
        config = load_config()
        try:
            yield config
        except SkipSave:
            return
        save_config(config)


def backup_config() -> str | None:
    if not CONFIG_FILE.exists():
        return None
    backup = CONFIG_FILE.with_suffix(".backup.json")
    try:
        shutil.copy2(CONFIG_FILE, backup)
        log.info(f"Config backed up to {backup.name}")
        return backup.name
    except Exception as e:
        log.error(f"Config backup failed: {e}")
        return None

def ensure_workspaces():
    with config_transaction() as config:
        workspaces = config.get("workspaces", [])
        if len(workspaces) >= DEFAULT_WORKSPACES:
            raise SkipSave
        while len(workspaces) < DEFAULT_WORKSPACES:
            idx = len(workspaces)
            name = WS_DEFAULT_NAMES[idx] if idx < len(WS_DEFAULT_NAMES) else f"Workspace {idx+1}"
            workspaces.append({"id": str(uuid.uuid4()), "name": name, "rooms": []})
        config["workspaces"] = workspaces
        log.info(f"Ensured {DEFAULT_WORKSPACES} workspaces exist")
