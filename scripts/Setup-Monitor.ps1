[CmdletBinding()]
param(
    [string]$DomainControllerFqdn = '',
    [string]$DomainName = ''
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$computer = Get-CimInstance Win32_ComputerSystem
if (-not $computer.PartOfDomain) {
    throw 'Run Setup-Monitor on a domain-joined Windows member computer, such as a lab management client.'
}
if (-not $DomainName) { $DomainName = $computer.Domain }
if (-not (Get-Command py -ErrorAction SilentlyContinue)) { throw 'Install Python 3.10+ for Windows with the Python launcher (py), then retry.' }
$pythonVersion = & py -3 -c "import sys; print(int(sys.version_info >= (3,10)))"
if ($LASTEXITCODE -ne 0 -or $pythonVersion -ne '1') { throw 'Python 3.10 or newer is required.' }
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    & py -3 -m venv (Join-Path $projectRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Creating the Python environment failed.' }
}
& $python -m pip install -r (Join-Path $projectRoot 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Installing Python dependencies failed.' }
$localPath = Join-Path $projectRoot 'monitor.local.json'
$newConfig = -not (Test-Path -LiteralPath $localPath)
$source = if ($newConfig) { Join-Path $projectRoot 'monitor.example.json' } else { $localPath }
$config = Get-Content -Raw -LiteralPath $source | ConvertFrom-Json
if ($newConfig -or $PSBoundParameters.ContainsKey('DomainName')) { $config.domain = $DomainName }
if ($newConfig -and -not $DomainControllerFqdn) {
    $record = Resolve-DnsName -Name "_ldap._tcp.dc._msdcs.$($config.domain)" -Type SRV -ErrorAction Stop |
        Where-Object { $_.Type -eq 'SRV' } | Sort-Object Priority | Select-Object -First 1
    if (-not $record.NameTarget) { throw 'No DC found through domain DNS. Supply -DomainControllerFqdn with the actual DC hostname.' }
    $DomainControllerFqdn = $record.NameTarget.TrimEnd('.')
}
if ($DomainControllerFqdn) { $config.domain_controller = $DomainControllerFqdn }
$config | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $localPath -Encoding UTF8
$credentialPath = $config.credential_file
if ($credentialPath.StartsWith('~/') -or $credentialPath.StartsWith('~\')) {
    $credentialPath = Join-Path $env:USERPROFILE $credentialPath.Substring(2)
}
$credential = Get-Credential -Message 'Enter the delegated monitoring account (user@example.test). Saved encrypted for this Windows user on this computer.'
if ($null -eq $credential) { throw 'No credential was supplied.' }
$credential | Export-Clixml -LiteralPath $credentialPath
Write-Host 'Local settings and encrypted credentials saved. Checking DC remoting...'
$arguments = @{ ComputerName = $config.domain_controller; Authentication = 'Kerberos'; Credential = $credential; ErrorAction = 'Stop' }
if ($config.use_ssl) { $arguments.UseSSL = $true }
try {
    Invoke-Command @arguments -ScriptBlock {
        Import-Module ActiveDirectory -ErrorAction Stop
        Get-ADDomain -Server $env:COMPUTERNAME | Select-Object DNSRoot
    }
    Write-Host 'DC connection passed. Run start-monitor.bat to open the dashboard.'
} catch {
    Write-Warning 'DC preflight failed. Verify its FQDN, domain DNS, WinRM and monitoring-account permissions. Settings were saved; the dashboard will show collection errors until corrected.'
}
