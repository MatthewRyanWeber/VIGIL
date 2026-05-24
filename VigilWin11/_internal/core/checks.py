import ssl, time, socket, platform, subprocess, threading, concurrent.futures
from urllib.request import urlopen, Request
from urllib.error import URLError
from core.config import (
    DEFAULT_PING_TIMEOUT, DEFAULT_TIMEOUT, MIN_TIMEOUT, MAX_TIMEOUT,
    DEFAULT_UDP_PORT, DEFAULT_SSH_PORT, DEFAULT_ROOM_INTERVAL,
    MIN_ROOM_INTERVAL, MAX_ROOM_INTERVAL, load_config, log,
)
from core.status import set_status, flush_status


def clamp(value, lo, hi, default):
    """Coerce value to the type of lo, then clamp to [lo, hi]. Returns default on failure."""
    try:    return max(lo, min(hi, type(lo)(value)))
    except (TypeError, ValueError): return default

# ─── Connectivity probes ─────────────────────────────────────────────────────

def _resolve(target: str) -> str:
    """Resolve a hostname to an IP for probes that need a raw address.
    Returns the input unchanged if it's already an IP."""
    try:
        return socket.getaddrinfo(target, None, socket.AF_INET, socket.SOCK_STREAM)[0][4][0]
    except socket.gaierror:
        return target

def ping_device(target: str, timeout: int = DEFAULT_PING_TIMEOUT):
    ip = _resolve(target)
    system = platform.system().lower()
    cmd = (["ping", "-n", "1", "-w", str(timeout * 1000), ip]
           if system == "windows" else
           ["ping", "-c", "1", "-W", str(timeout), ip])
    t0 = time.monotonic()
    try:
        r = subprocess.run(cmd, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=timeout + 1)
        ms = (time.monotonic() - t0) * 1000
        return r.returncode == 0, ms if r.returncode == 0 else None
    except Exception:
        return False, None

def udp_handshake(target: str, port: int, timeout: float = DEFAULT_TIMEOUT):
    ip = _resolve(target)
    t0 = time.monotonic()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(b"\x00" * 4, (ip, port))
        sock.recv(64)
        return True, (time.monotonic() - t0) * 1000
    except socket.timeout:
        return False, None
    except ConnectionRefusedError:
        return True, (time.monotonic() - t0) * 1000
    except OSError:
        return False, None
    finally:
        sock.close()

def ssh_check(target: str, port: int = 22, timeout: float = DEFAULT_TIMEOUT):
    ip = _resolve(target)
    t0 = time.monotonic()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((ip, port))
        sock.recv(256)
        return True, (time.monotonic() - t0) * 1000
    except socket.timeout:
        return False, None
    except (ConnectionRefusedError, OSError):
        return False, None
    finally:
        sock.close()

_no_verify_ctx = ssl.create_default_context()
_no_verify_ctx.check_hostname = False
_no_verify_ctx.verify_mode = ssl.CERT_NONE

def http_check(target: str, port: int = 80, timeout: float = DEFAULT_TIMEOUT):
    scheme = "https" if port == 443 else "http"
    url = f"{scheme}://{target}:{port}/"
    t0 = time.monotonic()
    try:
        req = Request(url, method="HEAD")
        urlopen(req, timeout=timeout, context=_no_verify_ctx)
        return True, (time.monotonic() - t0) * 1000
    except URLError:
        return False, None
    except Exception:
        return False, None

# ─── Check type registry ────────────────────────────────────────────────────
# Adding a new check type: add the probe function above, register it here,
# and add its key to VALID_CHECK_TYPES in config.py.

CHECK_REGISTRY = {
    "ping": lambda dev, t: ping_device(dev["ip"], int(round(t))),
    "udp":  lambda dev, t: udp_handshake(dev["ip"], int(dev.get("udp_port", DEFAULT_UDP_PORT)), t),
    "ssh":  lambda dev, t: ssh_check(dev["ip"], int(dev.get("ssh_port", DEFAULT_SSH_PORT)), t),
    "http": lambda dev, t: http_check(dev["ip"], int(dev.get("http_port", 80)), t),
}

def check_device(device: dict):
    target  = device.get("ip", "").strip()
    mode    = device.get("check_type", "ping")
    timeout = clamp(device.get("timeout", DEFAULT_TIMEOUT),
                    MIN_TIMEOUT, MAX_TIMEOUT, DEFAULT_TIMEOUT)
    if not target:
        return False, None
    probe = CHECK_REGISTRY.get(mode, CHECK_REGISTRY["ping"])
    return probe(device, timeout)

# ─── Poll engine ─────────────────────────────────────────────────────────────

class PollEngine:
    TICK           = 5
    MAX_CONCURRENT = 10

    def __init__(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._stop   = threading.Event()
        self._lock   = threading.Lock()
        self._next: dict = {}
        self._last_polled: dict = {}
        self._polling: set = set()
        self._semaphore = threading.Semaphore(self.MAX_CONCURRENT)

    def start(self):
        log.info("Poll engine started (max %d concurrent checks).", self.MAX_CONCURRENT)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=5)
        flush_status()
        log.info("Poll engine stopped.")

    def room_poll_info(self, room_id):
        with self._lock:
            now = time.monotonic()
            last = self._last_polled.get(room_id)
            nxt = self._next.get(room_id, 0.0)
            return {
                "polling": room_id in self._polling,
                "last_polled_secs_ago": round(now - last) if last is not None else None,
                "next_due_secs": max(0, round(nxt - now)),
            }

    def poll_now(self, room_id=None):
        with self._lock:
            if room_id:
                self._next[room_id] = 0.0
            else:
                for rid in list(self._next):
                    self._next[rid] = 0.0
        threading.Thread(target=self._tick, daemon=True).start()

    def _run(self):
        self._tick()
        while not self._stop.wait(self.TICK):
            self._tick()
            flush_status()

    def _tick(self):
        now    = time.monotonic()
        config = load_config()
        all_rooms = [r for ws in config.get("workspaces", []) for r in ws.get("rooms", [])]
        for room in all_rooms:
            rid      = room.get("id")
            interval = clamp(room.get("interval", DEFAULT_ROOM_INTERVAL),
                              MIN_ROOM_INTERVAL, MAX_ROOM_INTERVAL, DEFAULT_ROOM_INTERVAL)
            if not rid:
                continue
            with self._lock:
                if now < self._next.get(rid, 0.0):
                    continue
                if rid in self._polling:
                    continue
                self._polling.add(rid)
            threading.Thread(target=self._poll_room, args=(room, interval, now),
                             daemon=True).start()

    def _poll_room(self, room, interval, tick_time):
        rid = room.get("id")
        polled = 0
        try:
            for dev in room.get("devices", []):
                if self._stop.is_set():
                    return
                did = dev.get("id")
                if not did:
                    continue
                eff = dict(dev)
                if "check_type" not in eff:
                    eff["check_type"] = "ping"
                self._semaphore.acquire()
                try:
                    online, latency = check_device(eff)
                    set_status(did, "online" if online else "offline", latency)
                    polled += 1
                except Exception as e:
                    set_status(did, "unknown")
                    log.warning(f"Poll error [{dev.get('name','?')}]: {e}")
                finally:
                    self._semaphore.release()
        finally:
            with self._lock:
                self._next[rid] = tick_time + interval
                self._polling.discard(rid)
                self._last_polled[rid] = time.monotonic()
            if polled:
                log.info(f"Polled {polled} device(s) in {room.get('name', rid)}.")
