import sys, os, stat
from core.config import BASE_DIR, log

CERT_FILE  = BASE_DIR / "vigil-cert.pem"
KEY_FILE   = BASE_DIR / "vigil-key.pem"
TRUST_FLAG = BASE_DIR / ".vigil-trust-installed"


def _generate_cert() -> None:
    import datetime as dt, ipaddress
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key  = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Vigil Network Monitor")])
    now  = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + dt.timedelta(days=3650))
        .add_extension(
            x509.SubjectAlternativeName([
                x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
                x509.DNSName("localhost"),
            ]), critical=False)
        .add_extension(
            x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )

    CERT_FILE.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    KEY_FILE.write_bytes(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption()))
    try:
        os.chmod(KEY_FILE, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def _install_cert_windows() -> bool:
    if sys.platform != "win32":
        return False

    import subprocess as _sp
    cert_path = str(CERT_FILE.resolve()).replace("'", "''")
    ps_cmd = (
        f"Import-Certificate -FilePath '{cert_path}' "
        f"-CertStoreLocation Cert:\\CurrentUser\\Root"
    )

    try:
        result = _sp.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            log.info("Certificate installed to Windows trust store.")
            return True
        log.warning(f"Cert install failed: {result.stderr.strip()[:200]}")
        return False
    except Exception as e:
        log.warning(f"Cert install error: {e}")
        return False


def setup_https_cert():
    import ssl

    if not (CERT_FILE.exists() and KEY_FILE.exists()):
        log.info("Generating HTTPS certificate (one-time setup)...")
        _generate_cert()

    if not TRUST_FLAG.exists():
        if _install_cert_windows():
            log.info("Vigil is now trusted by Windows — no more certificate warnings.")
        else:
            log.info("Cert not auto-trusted. If Chrome warns, type 'thisisunsafe' on the page.")
        TRUST_FLAG.touch()

    ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ssl_ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ssl_ctx.load_cert_chain(str(CERT_FILE), str(KEY_FILE))
    return ssl_ctx


def _find_chrome_windows() -> str | None:
    paths = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ]
    local_appdata = os.environ.get("LOCALAPPDATA", "")
    if local_appdata:
        paths.extend([
            os.path.join(local_appdata, r"Google\Chrome\Application\chrome.exe"),
            os.path.join(local_appdata, r"Microsoft\Edge\Application\msedge.exe"),
            os.path.join(local_appdata, r"Chromium\Application\chrome.exe"),
        ])

    for p in paths:
        if os.path.exists(p):
            return p

    try:
        import winreg
        for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for subkey in (
                r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe",
                r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe",
            ):
                try:
                    with winreg.OpenKey(root, subkey) as k:
                        val, _ = winreg.QueryValueEx(k, None)
                        if val and os.path.exists(val):
                            return val
                except OSError:
                    continue
    except ImportError:
        pass

    return None


def launch_browser(url: str, use_https: bool) -> None:
    import webbrowser, shutil, subprocess as _sp

    chrome_flags = [
        "--allow-insecure-localhost",
        "--no-first-run",
        "--no-default-browser-check",
    ]

    exe = None

    if sys.platform == "win32":
        exe = _find_chrome_windows()
    elif sys.platform == "darwin":
        for path in (
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
        ):
            if os.path.exists(path):
                exe = path
                break
    else:
        for name in ("google-chrome", "google-chrome-stable", "chromium",
                     "chromium-browser", "microsoft-edge", "microsoft-edge-stable"):
            found = shutil.which(name)
            if found:
                exe = found
                break

    if exe:
        try:
            _sp.Popen([exe] + chrome_flags + [url],
                      stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
            log.info(f"Browser launched: {os.path.basename(exe)} (with cert bypass)")
            return
        except Exception as e:
            log.warning(f"Could not launch {exe}: {e} — falling back to default browser")
    else:
        log.warning("Chrome / Edge not found in standard locations.")
        log.warning("If the browser shows a cert warning, type 'thisisunsafe' on the page to bypass.")

    webbrowser.open(url)
    log.info("Browser opened via system default.")
