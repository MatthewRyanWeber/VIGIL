# Privacy Policy

**VIGIL Network Monitor** — Last updated: June 2, 2026

## Summary

VIGIL is a local network monitoring tool. It monitors device availability on your network. It does not collect, transmit, or store personal information.

## Data Collection

**We collect nothing.** VIGIL has no telemetry, analytics, usage tracking, crash reporting, or phone-home behavior.

| Category | Collected? | Details |
|----------|-----------|---------|
| Personal information | No | — |
| Usage data | No | — |
| Analytics / telemetry | No | — |
| Crash reports | No | — |
| IP addresses of users | No | — |
| Cookies | No | PIN-based sessions only, stored locally |
| Network device data | Local only | Device IPs, hostnames, and status stored on your machine |

## Data Processing

VIGIL performs the following local operations:

- **ICMP Ping** — Sends ping requests to devices you configure to check availability
- **HTTP/HTTPS checks** — Sends HTTP requests to devices you configure to verify web service status
- **SSH checks** — Tests SSH connectivity to devices you configure
- **UDP checks** — Sends UDP packets to devices you configure

All check results are processed and displayed locally. No device data is sent to any external service.

## Data Storage

All data is stored locally on your machine:

- **Device configuration** — JSON/YAML config files in the VIGIL working directory
- **Status history** — Device availability data stored locally
- **Session data** — Optional PIN-based authentication sessions, stored in memory

No data is stored in cloud services or remote databases.

## Network Access

VIGIL makes outbound connections only to the devices you explicitly configure for monitoring:

- ICMP ping to configured IP addresses
- HTTP/HTTPS requests to configured endpoints
- SSH connections to configured hosts
- UDP packets to configured ports

VIGIL does not contact any external servers, APIs, or services beyond your configured monitored devices.

## Web Interface

The VIGIL web UI binds to `127.0.0.1:9443` (localhost, HTTPS) by default. It can optionally be configured for LAN access. The UI displays device status and does not embed third-party scripts, trackers, or analytics.

## Third-Party Dependencies

VIGIL uses open-source Python packages (Flask, etc.). These packages do not collect data.

## Changes to This Policy

Changes will be documented in the repository's commit history.

## Contact

For privacy questions, open an issue at: https://github.com/MatthewRyanWeber/VIGIL/issues
