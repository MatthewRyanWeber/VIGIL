# Terms of Service

**VIGIL Network Monitor** — Last updated: June 2, 2026

## 1. Acceptance

By using VIGIL, you agree to these terms. If you do not agree, do not use the software.

## 2. License

VIGIL is distributed under the MIT License. You may use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the software, subject to the conditions in the LICENSE file.

## 3. Description of Service

VIGIL is a local network monitoring tool that checks the availability of devices on your network using ICMP ping, HTTP, SSH, and UDP checks. It runs on your machine and displays device status through a local web interface.

## 4. Your Responsibility

You are responsible for:

- **Network authorization** — Ensuring you have permission to monitor the devices you configure. Scanning or monitoring networks without authorization may violate applicable laws.
- **Network security** — Securing the VIGIL web interface if you enable LAN access. The default localhost-only binding is recommended.
- **PIN security** — If you enable PIN authentication, choosing a strong PIN and not sharing it.
- **Compliance** — Ensuring your use of VIGIL complies with your organization's network policies and applicable regulations.

## 5. No Warranty

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.

Specifically:
- VIGIL may fail to detect device outages or report false positives
- Status checks may be blocked by firewalls, VPNs, or network policies
- The software may consume network bandwidth during monitoring
- HTTPS certificates are self-signed by default and not trusted by browsers without manual acceptance

You should not rely on VIGIL as your sole monitoring solution for critical infrastructure.

## 6. Limitation of Liability

IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

This includes but is not limited to:
- Undetected outages or service failures
- Network disruption caused by monitoring checks
- Unauthorized access if the web interface is exposed to untrusted networks
- Data loss or system instability

## 7. Acceptable Use

Do not use VIGIL to:
- Monitor networks or devices you do not have authorization to monitor
- Conduct denial-of-service attacks by configuring excessively frequent checks
- Circumvent network security controls

## 8. Modifications

These terms may be updated in future releases. Changes will be documented in the repository's commit history.

## 9. Governing Law

These terms are governed by the laws of the State of New York, United States.

## Contact

For questions, open an issue at: https://github.com/MatthewRyanWeber/VIGIL/issues
