# Run locally as Administrator on a monitored Windows host after reviewing the scope.
# This changes remoting/firewall settings, never AD memberships or cluster state.
[CmdletBinding()]
param([Parameter(Mandatory = $true)][System.Net.IPAddress]$CollectorIPv4)
$ErrorActionPreference = 'Stop'
if ($CollectorIPv4.AddressFamily -ne [System.Net.Sockets.AddressFamily]::InterNetwork) { throw 'Supply one IPv4 address for the collector.' }
if (-not (Get-CimInstance Win32_ComputerSystem).PartOfDomain) { throw 'This helper is for domain-joined lab hosts only.' }
Enable-PSRemoting -Force
Get-NetFirewallRule -Name 'WINRM-HTTP-In-TCP*' | Set-NetFirewallRule -Profile Domain -RemoteAddress $CollectorIPv4.IPAddressToString
Write-Host 'WinRM HTTP firewall rules restricted to the collector IPv4 on the Domain profile.'
Write-Host 'No accounts or groups were changed. Delegate monitoring permissions separately.'
