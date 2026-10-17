# Monitoring mit dem Lab verbinden

Alle Netzwerkadressen, Domänen- und Servernamen in diesem Dokument sind anonymisierte Beispiele. Tatsächliche Verbindungsdaten gehören ausschließlich in die von Git ignorierte Datei `monitor.local.json`.

## Ausführungsort

Das Dashboard läuft auf einem **Windows-Mitgliedscomputer deiner Lab-Domäne**, beispielsweise einem Verwaltungsclient unter Hyper-V. Dieser Computer ist der Collector. Auf dem DC, den Clusterknoten oder dem Storage-Server muss dafür keine zusätzliche Serverrolle installiert werden.

Die Windows-VMs unter virt-manager werden über die bestehende Netzwerkverbindung abgefragt. SoftEther, VLANs, IP-Adressen, Datenträger und der Cluster werden für das Dashboard nicht neu aufgebaut.

## 1. Voraussetzungen

- Der Collector verwendet den internen Domänen-DNS, beispielsweise 10.10.10.10.
- Beide Standorte sind vom Collector erreichbar, insbesondere 10.20.40.10, .11, .12, .13 und der Dateidienst unter .20.
- Python 3.10 oder neuer einschließlich Python Launcher (`py`) und Git for Windows sind installiert.
- Ein dediziertes Domänenkonto besitzt die erforderlichen WinRM- und CIM-Leserechte. Eine Mitgliedschaft in Domain Admins ist keine Voraussetzung der Anwendung.

## 2. Windows-Zielcomputer vorbereiten

Wenn WinRM bereits erreichbar und korrekt berechtigt ist, muss es nicht erneut eingerichtet werden. Andernfalls die Datei `scripts/Enable-LabRemoting.ps1` auf den jeweiligen Zielcomputer kopieren und dort **lokal als Administrator** ausführen. YOUR-COLLECTOR-IP durch die tatsächliche Lab-IPv4-Adresse des Collectors ersetzen:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Enable-LabRemoting.ps1 -CollectorIPv4 YOUR-COLLECTOR-IP
```

Der Helfer aktiviert PowerShell-Remoting und beschränkt die üblichen WinRM-HTTP-Firewallregeln im Domänenprofil auf die angegebene Collector-Adresse. Bestehende administrative Zugriffe vor der Ausführung prüfen: Andere Administratoren können dieselben Regeln verwenden. Der Helfer verändert keine Konten, Gruppenmitgliedschaften oder Clusterressourcen.

Zugriff auf den PowerShell-Endpunkt und Leserechte für WMI/CIM unter `root\cimv2` müssen separat delegiert werden. Für Clusterinformationen kann ein Clusteradministrator mit `Grant-ClusterAccess -User DOMAIN\monitor-user -ReadOnly` Lesezugriff erteilen. AD-Replikationsdiagnosen können zusätzliche delegierte Verzeichnisrechte benötigen. Nicht lesbare Informationen erscheinen als unavailable; andere Messwerte bleiben sichtbar.

## 3. Dashboard auf dem Collector installieren

PowerShell unter dem Windows-Benutzer öffnen, der das Dashboard später ausführt:

```powershell
git clone https://github.com/Vahid-Rahmani/Automated-Hybrid-Network-Monitoring-Dashboard.git
cd Automated-Hybrid-Network-Monitoring-Dashboard
# Hier vor Setup die private monitor.local.json ablegen.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\Setup-Monitor.ps1
.\start-monitor.bat
```

Setup ermittelt einen tatsächlichen DC-FQDN anhand der DNS-SRV-Einträge der Domäne. Anschließend fragt es die Zugangsdaten des Monitoringkontos ab. Das Kennwort ausschließlich im Anmeldedialog eingeben. Die Credential-Datei wird mit Windows-DPAPI geschützt und ist an denselben Windows-Benutzer auf demselben Computer gebunden. Sie wird nicht in Git gespeichert.

Falls die DC-Ermittlung fehlschlägt, zuerst den internen Domänen-DNS prüfen. Ein bestimmter DC kann mit `-DomainControllerFqdn` angegeben werden. Dabei einen tatsächlichen DNS-Namen verwenden, keine IP-Adresse.

## 4. Dashboard öffnen

Im Browser **auf dem Collector** öffnen:

```text
http://127.0.0.1:8000
```

Den Abschluss des ersten Erfassungszyklus abwarten. Der nächste Zyklus beginnt standardmäßig 30 Sekunden nach Abschluss des vorherigen. Der Browser liest alle fünf Sekunden den gemeinsamen Snapshot; er startet keine zusätzlichen Abfragen gegen die Server.

Das Lab-Profil enthält:

| System | IPv4 / Ermittlung | Abfrage |
|---|---|---|
| Standort 1 | AD- und DNS-Ermittlung | Windows-Hostinformationen |
| STORAGE01 | 10.20.40.10 | Windows und iSCSI-Zieldienst |
| FS-NODE1 | 10.20.40.11 | Windows und Clusterinformationen |
| FS-NODE2 | 10.20.40.12 | Windows und Clusterinformationen |
| DC Standort 2 | 10.20.40.13 | LDAP-Port; Windows-Abfrage nach AD-Ermittlung des tatsächlichen Hostnamens |
| FILES | 10.20.40.20 | SMB-Endpunkt, TCP 445 |
| FILE-CLUSTER | 10.20.40.25 | RPC-Endpunkt, TCP 135 |

FILES und FILE-CLUSTER sind virtuelle Dienstidentitäten, keine zusätzlichen VMs mit eigenen CPU-/RAM-Werten. Rollenbesitz und Knotenzustand werden über FS-NODE1 und FS-NODE2 gelesen.

## 5. Meldungen einordnen

| Meldung | Nächste Prüfung |
|---|---|
| DNS error | Domänen-DNS, tatsächlichen Windows-Hostnamen und Management-DNS-Eintrag prüfen |
| TCP unreachable | Routing, Listener und Firewall des angegebenen Ports prüfen |
| WinRM / query error | Konto, Remoting-/CIM-Berechtigungen, Domänenmitgliedschaft und Kerberos prüfen |
| Some telemetry unavailable | Teilabfragen benötigen Rechte oder ein vorhandenes Dienstmodul |
| AD discovery error | DC-Verbindung und das ActiveDirectory-Modul auf dem DC prüfen |

Ein unbekannter Wert ist kein Verbrauch von null Prozent. Ein erreichbarer SMB-Port beweist weder Freigabeberechtigungen noch einen erfolgreichen Failover.

## 6. Abnahme und nächster Start

Einen Host aus jedem Standort prüfen. Anschließend die Rolle FILES mit einem berechtigten Konto im Failover Cluster Manager verschieben. Nach dem nächsten Erfassungszyklus muss der neue Owner sichtbar sein. Vom autorisierten Client aus weiterhin `\\FILES\Shares` öffnen und eine Testdatei lesen beziehungsweise ändern. AD-Replikation zusätzlich mit `repadmin /replsummary` unabhängig prüfen.

Zum Beenden in der Serverkonsole Ctrl+C drücken. Für spätere Starts genügt `start-monitor.bat`. Individuelle Einstellungen befinden sich in `monitor.local.json`; nach Änderungen das Dashboard neu starten.

Das Dashboard ist standardmäßig nur auf localhost erreichbar und besitzt keine Webanmeldung. Für die Lab-Abnahme den Browser direkt auf dem Collector verwenden und Port 8000 nicht öffentlich freigeben.
