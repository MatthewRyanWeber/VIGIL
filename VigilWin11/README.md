# Vigil for Windows 11

Standalone Windows build of Vigil -- no Python installation required.

---

## Quick Start

1. Download this entire `VigilWin11` folder
2. Double-click `Vigil.exe`
3. Chrome or Edge will open automatically to `https://127.0.0.1:9443`
4. If the browser shows a certificate warning, click **Advanced** then **Proceed to 127.0.0.1**

That's it. Vigil is running.

---

## What's in this folder

| File / Folder   | Purpose                                                  |
|-----------------|----------------------------------------------------------|
| `Vigil.exe`     | The application                                          |
| `config.json`   | Room and device configuration (edit via the Settings UI) |
| `_internal/`    | Bundled Python runtime and dependencies -- do not modify |

These files are created automatically on first run:

| File                      | Purpose                           |
|---------------------------|-----------------------------------|
| `vigil-cert.pem`          | Self-signed HTTPS certificate     |
| `vigil-key.pem`           | Certificate private key           |
| `vigil.log`               | Rotating log file                 |
| `status.json`             | Device status cache               |
| `.vigil-trust-installed`  | Tracks whether cert was installed |

---

## Command-Line Options

Open a Command Prompt in this folder and run:

```
Vigil.exe                     # Default: HTTPS on port 9443
Vigil.exe --port 9000         # Custom port
Vigil.exe --host 0.0.0.0     # Allow LAN access
Vigil.exe --no-https          # Plain HTTP (not recommended)
Vigil.exe --no-browser        # Don't auto-open the browser
```

---

## Status Colors

**Device LEDs:**

| Color  | Meaning                        |
|--------|--------------------------------|
| Blue   | Not yet checked                |
| Green  | Device is online               |
| Red    | Device is offline              |

**Room cards:**

| Color  | Meaning                                   |
|--------|-------------------------------------------|
| Blue   | No devices have been checked yet          |
| Green  | All devices in the room are online        |
| Amber  | Some devices online, some offline         |
| Red    | All devices in the room are offline       |

---

## Firewall

Windows Defender Firewall may prompt you the first time you run Vigil. Click **Allow access** for private networks. If you plan to access Vigil from other machines on your LAN, make sure to allow it on the appropriate network type.

If you accidentally blocked it, open **Windows Defender Firewall > Allow an app through firewall** and enable Vigil.

---

## Troubleshooting

**"Windows protected your PC" (SmartScreen)**

This appears because the exe is not code-signed. Click **More info** then **Run anyway**.

**Browser says "This site can't be reached"**

Make sure Vigil.exe is still running in the Command Prompt window. Check that the URL is `https://127.0.0.1:9443` (https, not http).

**"Your connection is not private"**

Expected on first visit. Vigil uses a self-signed certificate. Click **Advanced** then **Proceed to 127.0.0.1**.

**Port 9443 is already in use**

```
Vigil.exe --port 9000
```

Then open `https://127.0.0.1:9000`.

**Devices show offline but are reachable**

1. Verify the IP address in device settings
2. Try switching from ping to UDP, SSH, or HTTP check type
3. Increase the device timeout
4. Check that Windows Firewall allows outbound ICMP

**Vigil is slow to start with many devices**

Vigil limits concurrent checks to 10 to avoid flooding the network. Rooms transition from blue to their final color as results arrive. The first full cycle may take a minute with many rooms.

---

## Updating

To update Vigil, download the latest `VigilWin11` folder from GitHub and replace all files. Your `config.json` will be preserved if you copy it from the old folder to the new one before launching.

---

## Uninstalling

Delete the `VigilWin11` folder. Vigil does not write to the registry or install system services.
