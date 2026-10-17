# Read-only script transmitted to a Windows host with Invoke-Command -FilePath.
$ErrorActionPreference = 'Stop'
$checks = [ordered]@{}
$cpu = $null; $ram = $null; $uptime = $null; $systemDisk = $null
$disks = @(); $services = @(); $cluster = $null; $replication = $null
try {
    $os = Get-CimInstance Win32_OperatingSystem
    $ram = [math]::Round((1 - ($os.FreePhysicalMemory / $os.TotalVisibleMemorySize)) * 100, 1)
    $uptime = [math]::Round(((Get-Date) - $os.LastBootUpTime).TotalHours, 1)
    $checks.OS = 'ok'
} catch { $checks.OS = 'unavailable' }
try {
    # CIM property names are independent of German/English counter localisation.
    $samples = @(Get-CimInstance Win32_Processor | Where-Object { $null -ne $_.LoadPercentage })
    if ($samples.Count -eq 0) { throw 'No CPU samples' }
    $cpu = [math]::Round(($samples | Measure-Object LoadPercentage -Average).Average, 1)
    $checks.CPU = 'ok'
} catch { $checks.CPU = 'unavailable' }
try {
    $disks = @(Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' | Where-Object { $_.Size -gt 0 } | ForEach-Object {
        [pscustomobject]@{
            Name = $_.DeviceID; Label = $_.VolumeName
            SizeGB = [math]::Round($_.Size / 1GB, 2)
            FreeGB = [math]::Round($_.FreeSpace / 1GB, 2)
            UsedPercent = [math]::Round((1 - $_.FreeSpace / $_.Size) * 100, 1)
        }
    })
    $systemDisk = ($disks | Where-Object Name -eq $env:SystemDrive | Select-Object -First 1).UsedPercent
    $checks.Disks = 'ok'
} catch { $checks.Disks = 'unavailable' }
try {
    $names = @('NTDS', 'DNS', 'DHCPServer', 'Netlogon', 'LanmanServer', 'ClusSvc', 'MSiSCSI', 'WinTarget')
    $services = @(Get-Service | Where-Object { $_.Name -in $names } | ForEach-Object {
        [pscustomobject]@{ Name = $_.Name; Status = [string]$_.Status }
    })
    $checks.Services = 'ok'
} catch { $checks.Services = 'unavailable' }
if ($services.Name -contains 'ClusSvc') {
    try {
        Import-Module FailoverClusters -ErrorAction Stop
        $cluster = [pscustomobject]@{
            Name = (Get-Cluster).Name
            Nodes = @(Get-ClusterNode | ForEach-Object {
                [pscustomobject]@{ Name = $_.Name; State = [string]$_.State }
            })
            Groups = @(Get-ClusterGroup | ForEach-Object {
                [pscustomobject]@{ Name = $_.Name; State = [string]$_.State; OwnerNode = [string]$_.OwnerNode }
            })
        }
        $checks.Cluster = 'ok'
    } catch { $checks.Cluster = 'unavailable' }
}
if ($services.Name -contains 'NTDS') {
    try {
        Import-Module ActiveDirectory -ErrorAction Stop
        $failures = @(Get-ADReplicationFailure -Target $env:COMPUTERNAME -Scope Server)
        $replication = [pscustomobject]@{ FailureCount = $failures.Count }
        $checks.ADReplication = 'ok'
    } catch { $checks.ADReplication = 'unavailable' }
}
[pscustomobject]@{
    ComputerName = $env:COMPUTERNAME
    CPUPercent = $cpu; RAMUsedPercent = $ram; DiskUsedPercent = $systemDisk; UptimeHours = $uptime
    Disks = $disks; Services = $services; Cluster = $cluster; Replication = $replication; Checks = $checks
}
