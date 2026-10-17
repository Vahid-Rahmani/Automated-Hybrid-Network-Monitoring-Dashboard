# Automated Hybrid Network & Monitoring Dashboard

A self-hosted, read-only dashboard for a Windows lab across Hyper-V and Linux/KVM. It discovers domain computers through a DC and collects Windows telemetry with Kerberos-authenticated PowerShell remoting. The API and browser share one background collection loop.

All addresses, domain names and inventory names below are anonymized examples. Real connection settings belong in the ignored `monitor.local.json`, not in public commits.

**Run the collector on a domain-joined Windows member computer, such as MGMT01.** Linux can host the virtual machines and open the UI through an approved remote-access method, but this collector uses Windows PowerShell 5.1 and Windows-encrypted credentials. Run the dashboard browser on the collector for the default localhost deployment.

[Deutsche Anleitung: Verbindung mit dem Lab](docs/LAB-SETUP.de.md)

## What is implemented

- AD computer discovery, with role classification from AD properties rather than host-name guesses.
- Both sites mapped from the six configured IPv4 subnets; filtering by site, name/IP and attention status.
- CPU, RAM, system disk usage, uptime and all mounted fixed volumes.
- Status of installed AD DS, DNS, DHCP, SMB, iSCSI and cluster services.
- Cluster node states and role ownership, when the account can query Failover Clustering.
- AD replication failure-record count on DCs, when permitted. This is not a complete replication audit.
- Independent TCP checks for virtual cluster/SMB endpoints; they are not treated as extra Windows machines.
- Explicit DNS, connection, query, partial-data and stale-data states. Unknown metrics remain `null` / `—`.
- One bounded worker pool per cycle; HTTP refreshes never launch extra collectors.
- Discovery failure retains the previous inventory and displays the error.
- Safe DOM text rendering; no external JavaScript/CDN dependency.

## Lab inventory

| Site | Network | Purpose |
| --- | --- | --- |
| SITE-A | 10.10.10.0/24 | AD/DNS/DHCP and Hyper-V servers |
| SITE-A | 10.10.20.0/24, 10.10.30.0/24 | Client VLANs |
| SITE-B | 10.20.40.0/24 | Servers, cluster and iSCSI target |
| SITE-B | 10.20.50.0/24, 10.20.60.0/24 | Client VLANs |

| Name | IPv4 | Collection |
| --- | --- | --- |
| Site 1 domain computers | Discovered from AD/DNS | Windows telemetry |
| STORAGE01 | 10.20.40.10 | Windows telemetry + WinTarget service |
| FS-NODE1 | 10.20.40.11 | Windows telemetry + cluster details |
| FS-NODE2 | 10.20.40.12 | Windows telemetry + cluster details |
| Site 2 DC | 10.20.40.13 | LDAP TCP endpoint + Windows telemetry when AD discovers its actual hostname |
| FILES | 10.20.40.20 | TCP 445 endpoint only |
| FILE-CLUSTER | 10.20.40.25 | TCP 135 endpoint only |

Setup discovers the actual domain and DC FQDN using the collector's domain membership and DNS SRV records. Site labels are display mappings, not proof that AD Sites and Services has been configured.

## Connect the lab

1. On the Windows collector: join your lab domain, use internal domain DNS, and ensure the routed connection to both sites works. Install Python 3.10+ (including the `py` launcher) and Git.
2. Review and enable WinRM on the target hosts as described below. Delegate the monitoring account's permissions.
3. Place your private `monitor.local.json` in the cloned project folder before Setup, or copy `monitor.example.json` to that filename and replace its sample domain, inventory and networks. Open Windows PowerShell **as the Windows user that will run the dashboard**:

```powershell
git clone https://github.com/Vahid-Rahmani/Automated-Hybrid-Network-Monitoring-Dashboard.git
Set-Location Automated-Hybrid-Network-Monitoring-Dashboard
# Place your private monitor.local.json here before Setup.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\Setup-Monitor.ps1
.\start-monitor.bat
```

Setup installs the Python environment, creates `monitor.local.json`, discovers the DC FQDN and prompts for the monitoring credential. It exports the credential encrypted to `~/kurs-monitor-cred.xml`; that file can only be decrypted by the same Windows user on the same computer. Existing local inventory settings are preserved on a later setup run. An explicit DC override is available:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\Setup-Monitor.ps1 -DomainControllerFqdn YOUR-REAL-DC.example.test
```

Open <http://127.0.0.1:8000>. The first cycle can take longer than the 30-second delay between completed cycles. The launcher shows its server console; close that console or press Ctrl+C there to stop it. No automatic Windows service or scheduled task is installed.

### Target remoting and permissions

On each Windows target that you want to monitor, run the following **locally as Administrator**, substituting the collector's actual lab IPv4:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\Enable-LabRemoting.ps1 -CollectorIPv4 YOUR-COLLECTOR-IP
```

The helper enables PowerShell remoting and restricts the standard WinRM HTTP firewall rules to that IPv4 on the Domain profile. Review existing management access first: these rules might already serve other administrators. No wildcard TrustedHosts, Basic authentication, credential delegation, certificate bypass or firewall shutdown is used. With Kerberos, WinRM encrypts messages even over its HTTP transport. `use_ssl: true` supports an already configured, trusted HTTPS listener on 5986; the helper does not provision certificates.

Use a dedicated domain monitoring account. AD inventory normally needs read access; WinRM endpoint access and remote CIM access are separate permissions. Delegate through Remote Management Users and the required PowerShell endpoint / `root\cimv2` permissions, according to your lab policy. On DCs, use AD-managed groups rather than trying to create local users. Do not add the account to Domain Admins just to clear a dashboard error. A constrained/JEA endpoint is a further hardening task; it is not provisioned by this repository.

For cluster details, delegate read-only cluster access once using `Grant-ClusterAccess -User DOMAIN\monitor-user -ReadOnly` from an authorised cluster administrator session. The monitoring account also needs the remoting/CIM permissions above. AD replication diagnostics can need extra delegated directory permissions; the UI shows unavailable if those queries are denied, while retaining other metrics. No setup script changes account/group permissions automatically.

### Configuration

Edit `monitor.local.json`, not Python code. Restart the dashboard after edits. `MONITOR_CONFIG` can select another JSON file explicitly; a missing explicitly selected file is an error.

- `domain_controller`: actual FQDN for Kerberos; IP addresses are rejected.
- `networks`: site-to-subnet display mappings.
- `devices`: static Windows targets and TCP-only service endpoints. Static metadata overrides AD discovery for the same FQDN.
- `services`: required service names for a static host.
- `refresh_seconds`, `max_workers`, `timeout_seconds`: bounded polling controls.
- `credential_file`: local encrypted credential path, never a plaintext password.

Windows hostname and libvirt/Hyper-V VM name can differ. Use the actual Windows DNS hostname. For a static Windows host, DNS resolving to a different configured IPv4 is reported as an error rather than querying the wrong host. A multihomed machine must resolve to the intended management address.

## Validation and status meanings

```powershell
.\.venv\Scripts\python.exe collector.py
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

API routes: `/api/status` (cached snapshot and diagnostics), `/api/devices` (legacy list shape), `/health` (API/collector lifecycle).

| Display | Meaning |
| --- | --- |
| Telemetry OK | Host query completed with the expected metric fields |
| Warning | Partial telemetry, high usage, stopped required service or a reported cluster/AD failure |
| TCP reachable | Endpoint port is open; does not prove service functionality |
| TCP unreachable | Port could not be reached; does not prove the host is powered off |
| DNS error | Missing or mismatching management DNS |
| WinRM / query error | Port responded, but authenticated telemetry failed |
| Waiting / stale | No completed recent cycle; previous readings are not current |

Before calling the **lab** complete, confirm real values for a host in each site, read cluster role ownership, move FILES between nodes manually and verify the same SMB share from an authorised client. Check AD replication independently with `repadmin`. Automated tests use mocked host results; they do not constitute access to the private lab.

## Scope

This is a local lab dashboard, bound to `127.0.0.1`. It has no web authentication; do not expose its port publicly. Cisco/Linux/EVE and SoftEther service telemetry, historical storage, notifications, automatic remediation and high availability for the monitoring application remain outside this implementation. A TCP endpoint is available for an explicitly configured infrastructure service, but it does not inspect its configuration.

Repository files: `config.py`, `collector.py`, `monitor.py`, `app.py`, `monitor.example.json`, `static/`, `scripts/`, `tests/` and `.github/workflows/test.yml`. There is no database or frontend build step.

## References

- [PowerShell Invoke-Command and IP/Kerberos restrictions](https://learn.microsoft.com/powershell/module/microsoft.powershell.core/invoke-command)
- [Windows credential export scope](https://learn.microsoft.com/powershell/module/microsoft.powershell.utility/export-clixml)
- [Remoting security and encrypted messages](https://learn.microsoft.com/powershell/scripting/security/remoting/winrm-security)
- [Grant-ClusterAccess](https://learn.microsoft.com/powershell/module/failoverclusters/grant-clusteraccess)

No license file is currently published. Review the project before commercial reuse.
