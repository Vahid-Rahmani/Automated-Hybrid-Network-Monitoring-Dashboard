"""Read-only AD discovery and Windows telemetry over authenticated WinRM."""
import base64
import json
import logging
import math
import os
import socket
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from config import HOST, ROOT, load_config, site_for_ip

logger = logging.getLogger(__name__)


class CollectionError(RuntimeError):
    pass


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def ps_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def run_powershell(script, timeout=20):
    if os.name != "nt":
        raise CollectionError("Run the collector on a domain-joined Windows machine with Windows PowerShell 5.1")
    prefix = "$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue'; [Console]::OutputEncoding=[System.Text.UTF8Encoding]::new(); "
    encoded = base64.b64encode((prefix + script).encode("utf-16le")).decode("ascii")
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            capture_output=True, encoding="utf-8", errors="replace", timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise CollectionError("PowerShell query timed out") from exc
    except OSError as exc:
        raise CollectionError("Windows PowerShell could not be started") from exc
    if result.returncode:
        raise CollectionError("PowerShell query failed: check credentials, Kerberos/DNS, WinRM permissions and the target service")
    try:
        return json.loads(result.stdout.strip().lstrip("\ufeff"))
    except (ValueError, TypeError) as exc:
        raise CollectionError("PowerShell returned invalid JSON") from exc


def remote_script(hostname, config, body=None, file_path=None):
    command = (
        f"$cred = Import-Clixml -LiteralPath {ps_quote(config['credential_file'])}; "
        "$options = New-PSSessionOption -OpenTimeout 5000 -OperationTimeout 10000; "
        f"$result = Invoke-Command -ComputerName {ps_quote(hostname)} "
        "-Authentication Kerberos -Credential $cred -SessionOption $options "
    )
    if config["use_ssl"]:
        command += "-UseSSL "
    if file_path:
        command += f"-FilePath {ps_quote(file_path)}; "
    else:
        command += "-ScriptBlock { $ErrorActionPreference='Stop'; " + body + " }; "
    return command + "ConvertTo-Json -InputObject $result -Depth 8 -Compress"


def discover_ad_computers(config):
    body = (
        "Import-Module ActiveDirectory -ErrorAction Stop; "
        "Get-ADComputer -Server $env:COMPUTERNAME -Filter 'Enabled -eq $true' "
        "-Properties DNSHostName,OperatingSystem,PrimaryGroupID | "
        "Select-Object Name,DNSHostName,OperatingSystem,PrimaryGroupID"
    )
    data = run_powershell(remote_script(config["domain_controller"], config, body=body), config["timeout_seconds"])
    if data is None:
        return []
    rows = [data] if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise CollectionError("AD discovery returned an unexpected result")
    endpoints = {d.get("hostname", "").casefold() for d in config["devices"] if d["kind"] == "tcp"}
    devices = []
    for row in rows:
        hostname = row.get("DNSHostName")
        if not hostname or not HOST.fullmatch(hostname) or hostname.casefold() in endpoints:
            continue
        role = "Domain Controller" if row.get("PrimaryGroupID") in (516, 521) else (
            "Server" if "server" in (row.get("OperatingSystem") or "").lower() else "Client"
        )
        devices.append({"name": row["Name"], "hostname": hostname, "kind": "windows", "type": role})
    return devices


def resolve_ip(hostname):
    try:
        return socket.getaddrinfo(hostname, None, socket.AF_INET, socket.SOCK_STREAM)[0][4][0]
    except (OSError, IndexError):
        return None


def probe_tcp(address, port, timeout=2):
    try:
        with socket.create_connection((address, port), timeout=timeout):
            return True
    except OSError:
        return False


def number(value, percent=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    if value < 0 or (percent and value > 100):
        return None
    return round(value, 1)


def collect_one_device(device, config):
    hostname = device.get("hostname", "")
    expected_ip = device.get("ip")
    resolved = resolve_ip(hostname) if hostname else expected_ip
    ip = resolved or expected_ip
    kind = device.get("kind", "windows")
    port = device.get("port", 5986 if config["use_ssl"] else 5985)
    row = {
        "name": device["name"], "hostname": hostname, "ip": ip,
        "type": device.get("type", "Windows Host"), "kind": kind,
        "site": site_for_ip(ip, config), "port": port,
        "online": False, "status": "unreachable", "error": None,
        "cpu": None, "ram": None, "disk": None, "uptime": None,
        "disks": [], "services": [], "cluster": None, "replication": None,
        "checks": {}, "alerts": [], "checked_at": utc_now(),
    }
    if kind == "windows" and not resolved:
        row.update(status="dns_error", error="Hostname does not resolve through the collector's DNS")
        return row
    if expected_ip and resolved and resolved != expected_ip:
        row.update(status="dns_error", error=f"DNS differs from configured IP {expected_ip}; query skipped")
        return row
    if not ip or not probe_tcp(ip, port):
        row["error"] = f"TCP {port} unreachable; this does not prove the machine is powered off"
        return row
    row["online"] = True
    if kind == "tcp":
        row["status"] = "reachable"
        return row
    try:
        metrics = run_powershell(remote_script(hostname, config, file_path=ROOT / "scripts" / "collect-host.ps1"), config["timeout_seconds"])
        if not isinstance(metrics, dict) or not metrics.get("ComputerName"):
            raise CollectionError("Host query returned an unexpected result")
        for dest, source in (("cpu", "CPUPercent"), ("ram", "RAMUsedPercent"), ("disk", "DiskUsedPercent"), ("uptime", "UptimeHours")):
            row[dest] = number(metrics.get(source), percent=dest != "uptime")
        for dest, source, default in (("disks", "Disks", []), ("services", "Services", []), ("cluster", "Cluster", None), ("replication", "Replication", None), ("checks", "Checks", {})):
            row[dest] = metrics.get(source) or default
        row["status"] = "ok"
        known = {service["Name"]: service for service in row["services"]}
        required = set(device.get("services", [])) | ({"NTDS", "DNS", "DHCPServer", "WinTarget", "ClusSvc"} & known.keys())
        for name in sorted(required):
            if name not in known or known[name]["Status"] != "Running":
                row["alerts"].append(f"Required service {name} is not running or could not be read")
        for key in ("cpu", "ram", "disk"):
            if row[key] is not None and row[key] >= 85:
                row["alerts"].append(f"{key.upper()} usage >= 85%")
        for disk in row["disks"]:
            used = number(disk.get("UsedPercent"), percent=True)
            if used is not None and used >= 85:
                row["alerts"].append(f"Volume {disk.get('Name', '?')} usage >= 85%")
        if row["replication"] and row["replication"].get("FailureCount", 0) > 0:
            row["alerts"].append("Active Directory reports replication failures")
        if row["cluster"]:
            for node in row["cluster"].get("Nodes", []):
                if node.get("State") != "Up":
                    row["alerts"].append(f"Cluster node {node.get('Name')} is {node.get('State')}")
            for group in row["cluster"].get("Groups", []):
                if group.get("State") in ("Failed", "PartialOnline"):
                    row["alerts"].append(f"Cluster role {group.get('Name')} is {group.get('State')}")
        if any(row[key] is None for key in ("cpu", "ram", "disk", "uptime")) or any(value != "ok" for value in row["checks"].values()):
            row["alerts"].append("Some telemetry could not be collected")
        if row["alerts"]:
            row["status"] = "warning"
    except CollectionError as exc:
        row.update(status="metrics_error", error=str(exc))
    return row


def merge_inventory(configured, discovered):
    inventory = {}
    for device in discovered + configured:
        key = (device.get("hostname") or device["name"]).casefold().rstrip(".")
        inventory[key] = {**inventory.get(key, {}), **device}
    return list(inventory.values())


def collect_snapshot(config=None, previous=None):
    config = config or load_config()
    started = time.monotonic()
    errors = []
    try:
        discovered = discover_ad_computers(config)
    except CollectionError as exc:
        errors.append("AD discovery: " + str(exc))
        discovered = [{key: row[key] for key in ("name", "hostname", "kind", "type")} for row in (previous or []) if row.get("hostname")]
    dc = config["domain_controller"]
    seed_dc = {"name": dc.split('.')[0], "hostname": dc, "kind": "windows", "type": "Domain Controller"}
    inventory = merge_inventory([seed_dc] + config["devices"], discovered)
    devices = []
    with ThreadPoolExecutor(max_workers=config["max_workers"]) as executor:
        futures = {executor.submit(collect_one_device, device, config): device for device in inventory}
        for future in as_completed(futures):
            device = futures[future]
            try:
                devices.append(future.result())
            except Exception:
                logger.exception("Host collection failed for %s", device['name'])
                devices.append({
                    "name": device["name"], "hostname": device.get("hostname", ""),
                    "kind": device.get("kind", "windows"), "type": device.get("type", "Windows Host"),
                    "ip": device.get("ip"), "site": site_for_ip(device.get("ip"), config),
                    "status": "collection_error", "error": "Unexpected host collection error",
                    "online": False, "cpu": None, "ram": None, "disk": None, "uptime": None,
                    "alerts": [], "checked_at": utc_now(),
                })
    devices.sort(key=lambda item: (item["site"], item["name"].casefold()))
    return {"devices": devices, "errors": errors, "updated_at": utc_now(), "duration_seconds": round(time.monotonic() - started, 2)}


def collect_status():
    """Compatibility entry point for existing scripts."""
    return collect_snapshot()["devices"]


if __name__ == "__main__":
    print(json.dumps(collect_snapshot(), indent=2))
