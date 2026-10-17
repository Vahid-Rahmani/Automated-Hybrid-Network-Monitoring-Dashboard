"""Validated local connection settings. Passwords belong in Windows CLIXML."""
import ipaddress
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HOST = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]{0,252}$")


def dns_name(value):
    if not isinstance(value, str) or not HOST.fullmatch(value):
        return False
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return True
    return False


def load_config(path=None):
    selected = Path(path or os.environ.get("MONITOR_CONFIG") or ROOT / "monitor.local.json").expanduser()
    if not selected.is_file():
        if path or os.environ.get("MONITOR_CONFIG"):
            raise ValueError(f"Configuration file not found: {selected}")
        selected = ROOT / "monitor.example.json"
    data = json.loads(selected.read_text(encoding="utf-8-sig"))
    for key in ("domain", "domain_controller"):
        if not dns_name(data.get(key)):
            raise ValueError(f"{key} must be a DNS name, not an IP (Kerberos)")
    for key, default, low, high in (("refresh_seconds", 30, 10, 3600), ("max_workers", 4, 1, 16), ("timeout_seconds", 20, 5, 60)):
        value = data.get(key, default)
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f"{key} must be an integer between {low} and {high}")
        data[key] = value
    data.setdefault("use_ssl", False)
    if type(data["use_ssl"]) is not bool:
        raise ValueError("use_ssl must be true or false")
    data["credential_file"] = str(Path(data.get("credential_file", "~/kurs-monitor-cred.xml")).expanduser())
    data.setdefault("networks", [])
    networks = []
    for item in data["networks"]:
        network = ipaddress.ip_network(item["prefix"], strict=True)
        if network.version != 4 or not item.get("site"):
            raise ValueError("Each network requires an IPv4 prefix and a site")
        if any(network.overlaps(other) for other in networks):
            raise ValueError("Site prefixes must not overlap")
        networks.append(network)
    data.setdefault("devices", [])
    seen = set()
    for device in data["devices"]:
        if not isinstance(device.get("name"), str) or not device["name"].strip():
            raise ValueError("Each device requires a name")
        key = device["name"].casefold()
        if key in seen:
            raise ValueError("Device names must be unique")
        seen.add(key)
        kind = device.setdefault("kind", "windows")
        if kind not in ("windows", "tcp"):
            raise ValueError("Device kind must be windows or tcp")
        hostname = device.get("hostname", "")
        if hostname and not dns_name(hostname):
            raise ValueError("Device hostname must be a DNS name")
        if kind == "windows" and not hostname:
            raise ValueError("Windows devices require a hostname for Kerberos")
        if device.get("ip"):
            ipaddress.IPv4Address(device["ip"])
        if not hostname and not device.get("ip"):
            raise ValueError("Device requires hostname or IP")
        port = device.setdefault("port", 5986 if data["use_ssl"] else 5985)
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("Invalid TCP port")
    return data


def site_for_ip(address, config):
    try:
        address = ipaddress.ip_address(address)
    except (ValueError, TypeError):
        return "Unmapped"
    for item in config["networks"]:
        if address in ipaddress.ip_network(item["prefix"]):
            return item["site"]
    return "Unmapped"
