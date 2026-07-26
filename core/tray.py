"""System-tray icon: an LED that mirrors the room-card status colors.

Optional feature. pystray and Pillow are declared in requirements-windows.txt
rather than requirements.txt, so `available()` is checked before use and the
app runs unchanged when they're absent.
"""

import threading

from core.config import APP_NAME, VERSION, load_config, log
from core.status import get_all_public_status
from core import autostart

REFRESH_SECS = 10

# sRGB conversions of the oklch palette in static/vigil.css, so the tray LED
# and the in-app LEDs are the same colors.
GREEN   = (75, 181, 97)      # all devices online
RED     = (229, 77, 69)      # all devices offline
AMBER   = (227, 147, 57)     # mixed
BLUE    = (57, 134, 228)     # nothing checked yet
GREY    = (124, 118, 114)    # no devices configured

_icon = None


def available() -> bool:
    try:
        import pystray  # noqa: F401
        from PIL import Image  # noqa: F401
        return True
    except ImportError:
        return False


# ─── Status roll-up ─────────────────────────────────────────────────────────

def _counts() -> tuple[int, int, int]:
    """(configured, online, offline) across every workspace."""
    config = load_config()
    ids = [
        d.get("id")
        for ws in config.get("workspaces", [])
        for room in ws.get("rooms", [])
        for d in room.get("devices", [])
        if d.get("id")
    ]
    statuses = get_all_public_status()
    online  = sum(1 for i in ids if statuses.get(i, {}).get("status") == "online")
    offline = sum(1 for i in ids if statuses.get(i, {}).get("status") == "offline")
    return len(ids), online, offline


def _color(total: int, online: int, offline: int) -> tuple[int, int, int]:
    if total == 0:              return GREY
    if online + offline == 0:   return BLUE
    if offline == 0:            return GREEN
    if online == 0:             return RED
    return AMBER


def _tooltip(total: int, online: int, offline: int) -> str:
    if total == 0:
        return f"{APP_NAME} {VERSION} — no devices configured"
    pending = total - online - offline
    parts = [f"{online} online", f"{offline} offline"]
    if pending:
        parts.append(f"{pending} pending")
    return f"{APP_NAME} {VERSION} — " + ", ".join(parts)


# ─── Icon rendering ─────────────────────────────────────────────────────────

def _image(color: tuple[int, int, int]):
    """An LED dot on a transparent field, drawn at 4x and downsampled so the
    edges stay smooth at whatever size the shell asks for."""
    from PIL import Image, ImageDraw

    size, scale = 64, 4
    img  = Image.new("RGBA", (size * scale, size * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    pad  = 6 * scale

    # Dark halo first: keeps the dot legible on a light taskbar.
    draw.ellipse([pad - 2 * scale, pad - 2 * scale,
                  size * scale - pad + 2 * scale, size * scale - pad + 2 * scale],
                 fill=(18, 18, 20, 90))
    draw.ellipse([pad, pad, size * scale - pad, size * scale - pad],
                 fill=color + (255,))
    # Offset highlight, matching the glossy LEDs in the web UI.
    hl = size * scale // 4
    draw.ellipse([hl, hl - 2 * scale, hl + 9 * scale, hl + 7 * scale],
                 fill=(255, 255, 255, 70))

    return img.resize((size, size), Image.LANCZOS)


# ─── Menu ───────────────────────────────────────────────────────────────────

def _build_menu(open_ui, quit_app):
    import pystray

    def status_text(_item):
        total, online, offline = _counts()
        if total == 0:
            return "No devices configured"
        return f"{online} online · {offline} offline · {total} total"

    items = [
        pystray.MenuItem("Open Vigil", lambda *_: open_ui(), default=True),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(status_text, None, enabled=False),
        pystray.Menu.SEPARATOR,
    ]

    if autostart.supported():
        items.append(pystray.MenuItem(
            "Start Vigil when I sign in",
            lambda *_: autostart.toggle(),
            checked=lambda _item: autostart.is_enabled(),
        ))
        items.append(pystray.Menu.SEPARATOR)

    items.append(pystray.MenuItem("Quit Vigil", lambda *_: quit_app()))
    return pystray.Menu(*items)


# ─── Lifecycle ──────────────────────────────────────────────────────────────

def _refresh_loop(icon, stop_event):
    while not stop_event.wait(REFRESH_SECS):
        try:
            total, online, offline = _counts()
            icon.icon  = _image(_color(total, online, offline))
            icon.title = _tooltip(total, online, offline)
            icon.update_menu()
        except Exception as e:
            log.warning(f"Tray refresh failed: {e}")


def run(open_ui, on_quit) -> None:
    """Show the tray icon and block until the user picks Quit.

    open_ui: called when the icon is double-clicked or 'Open Vigil' is picked.
    on_quit: called once, on the way out, before this returns.
    """
    global _icon
    import pystray

    stop_event = threading.Event()

    def quit_app():
        stop_event.set()
        if _icon is not None:
            _icon.stop()

    total, online, offline = _counts()
    _icon = pystray.Icon(
        "vigil",
        icon=_image(_color(total, online, offline)),
        title=_tooltip(total, online, offline),
        menu=_build_menu(open_ui, quit_app),
    )

    threading.Thread(target=_refresh_loop, args=(_icon, stop_event),
                     daemon=True).start()

    log.info("Tray icon active — Vigil keeps running in the notification area.")
    try:
        _icon.run()
    finally:
        stop_event.set()
        on_quit()
