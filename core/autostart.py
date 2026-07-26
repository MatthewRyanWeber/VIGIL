"""Start-at-login toggle, backed by the HKCU Run key.

Windows only — every function is a safe no-op elsewhere so callers don't
need platform guards. Per-user (HKCU), so no admin rights are required.
"""

import sys
from pathlib import Path

from core.config import log

RUN_KEY    = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "Vigil"


def supported() -> bool:
    return sys.platform == "win32"


def launch_command() -> str:
    """Command line that relaunches this exact install, tray-only and quiet."""
    if getattr(sys, "frozen", False):
        return f'"{Path(sys.executable).resolve()}" --tray --no-browser'
    # Source checkout: prefer pythonw.exe so login doesn't flash a console.
    py = Path(sys.executable).resolve()
    pyw = py.with_name("pythonw.exe")
    if pyw.exists():
        py = pyw
    script = Path(__file__).resolve().parent.parent / "vigil.py"
    return f'"{py}" "{script}" --tray --no-browser'


def _stored_command() -> str | None:
    if not supported():
        return None
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, VALUE_NAME)
            return value or None
    except OSError:
        return None


def is_enabled() -> bool:
    return _stored_command() is not None


def enable() -> bool:
    if not supported():
        return False
    import winreg
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, launch_command())
        log.info("Start-at-login enabled.")
        return True
    except OSError as e:
        log.warning(f"Could not enable start-at-login: {e}")
        return False


def disable() -> bool:
    if not supported():
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, VALUE_NAME)
        log.info("Start-at-login disabled.")
        return True
    except FileNotFoundError:
        return True          # already gone
    except OSError as e:
        log.warning(f"Could not disable start-at-login: {e}")
        return False


def toggle() -> bool:
    """Flip the setting. Returns the new state."""
    if is_enabled():
        disable()
        return False
    enable()
    return is_enabled()


def sync() -> None:
    """Repoint a stale Run entry at the current install.

    Without this, moving or reinstalling Vigil leaves a login entry aimed at
    a path that no longer exists, and autostart silently stops working.
    """
    stored = _stored_command()
    if stored is None:
        return
    current = launch_command()
    if stored != current:
        log.info("Start-at-login entry was stale — repointing at this install.")
        enable()
