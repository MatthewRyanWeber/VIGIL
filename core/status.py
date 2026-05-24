import json, threading
from datetime import datetime, timezone
from core.config import STATUS_FILE, log

_status_lock = threading.Lock()
_status: dict = {}
_dirty = False

def get_status(device_id: str) -> dict:
    with _status_lock:
        return _status.get(device_id, {
            "status":         "pending",
            "last_checked":   None,
            "latency_ms":     None,
            "online_since":   None,
            "avg_latency_ms": None,
        })

def set_status(device_id: str, status: str, latency_ms=None) -> None:
    global _dirty
    with _status_lock:
        prev = _status.get(device_id, {})
        now_iso = datetime.now(timezone.utc).isoformat()

        if status == "online":
            online_since = prev.get("online_since") if prev.get("status") == "online" else now_iso
        else:
            online_since = None

        history = prev.get("_latency_history", [])
        if latency_ms is not None:
            history = (history + [round(latency_ms, 1)])[-10:]
        avg = round(sum(history) / len(history), 1) if history else None

        _status[device_id] = {
            "status":           status,
            "last_checked":     now_iso,
            "latency_ms":       round(latency_ms, 1) if latency_ms is not None else None,
            "online_since":     online_since,
            "avg_latency_ms":   avg,
            "_latency_history": history,
        }
        _dirty = True

def public_status(device_id: str) -> dict:
    s = dict(get_status(device_id))
    s.pop("_latency_history", None)
    return s

def get_all_public_status() -> dict:
    with _status_lock:
        out = {}
        for k, v in _status.items():
            s = dict(v)
            s.pop("_latency_history", None)
            out[k] = s
        return out

def load_status() -> None:
    global _dirty
    if not STATUS_FILE.exists():
        return
    try:
        data = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
        with _status_lock:
            _status.update(data)
            _dirty = False
        log.info(f"Restored status for {len(data)} device(s).")
    except Exception as e:
        log.warning(f"Could not load status file: {e}")

def flush_status() -> None:
    global _dirty
    with _status_lock:
        if not _dirty:
            return
        snapshot = {k: dict(v) for k, v in _status.items()}
        _dirty = False
    tmp = STATUS_FILE.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
        tmp.replace(STATUS_FILE)
    except Exception as e:
        log.warning(f"Status flush failed: {e}")
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
