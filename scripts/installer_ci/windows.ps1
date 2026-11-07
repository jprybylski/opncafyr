# CI (#19, #36): the installer end to end on a Windows runner, as a new standard user.
#
#   powershell -File scripts\installer_ci\windows.ps1 -Payload <dir> -Newer <dir> -Assets <dir> -Results <dir>
#
# The runner (an administrator) only sets up: it creates a local standard user whose
# name contains a space, lets it run batch jobs, and starts each leg as a scheduled task
# under that user (-RunLevel Limited). Every installer step runs as that user.
# -Assets holds the private opnca-r files (manifest.json and the Windows binary), which
# the installer takes through --assets. -Newer is a payload built for a later version
# (the offline update legs).
#
# #36: the runner builds the offline bundle twice (independent download caches) and
# compares the manifests; then a per-user outbound firewall rule blocks the user from
# every non-loopback address, with dropped packets logged, for the offline legs. A
# deliberate attempt first proves that the log records drops; the offline legs must
# then leave none.
param(
    [Parameter(Mandatory = $true)][string]$Payload,
    [Parameter(Mandatory = $true)][string]$Newer,
    [Parameter(Mandatory = $true)][string]$Assets,
    [Parameter(Mandatory = $true)][string]$Results,
    # #34: only install, then repair --build-from-source (the 'source' leg)
    [switch]$SourceBuild
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$user = 'opncafyr ci'
$work = 'C:\opncafyr-ci'
New-Item -ItemType Directory -Force $Results | Out-Null
$Results = (Resolve-Path $Results).Path

function Fail([string]$msg) { Write-Host "::error::$msg"; exit 1 }

# ---- runner setup ------------------------------------------------------------------------
$machinePathBefore = [Environment]::GetEnvironmentVariable('Path', 'Machine')
$hklmRBefore = (reg query 'HKLM\SOFTWARE\R-core\R' /s 2>$null | Out-String)
Add-Type -AssemblyName System.Web
$pw = [System.Web.Security.Membership]::GeneratePassword(24, 4) + 'aA1!'
$secure = ConvertTo-SecureString $pw -AsPlainText -Force
if (-not (Get-LocalUser -Name $user -ErrorAction SilentlyContinue)) {
    New-LocalUser -Name $user -Password $secure -PasswordNeverExpires -AccountNeverExpires | Out-Null
}
Add-LocalGroupMember -Group 'Users' -Member $user -ErrorAction SilentlyContinue
if (Get-LocalGroupMember -Group 'Administrators' | Where-Object { $_.Name -like "*\$user" }) { Fail "$user is an administrator" }
$sid = (Get-LocalUser -Name $user).SID.Value
# Windows Server doesn't let Users log on as a batch job (scheduled tasks); allow this one.
$inf = Join-Path $env:TEMP 'rights.inf'
secedit /export /cfg $inf /areas USER_RIGHTS | Out-Null
$lines = Get-Content $inf
$found = $false
$lines = $lines | ForEach-Object {
    if ($_ -match '^SeBatchLogonRight\s*=') { $found = $true; "$_,*$sid" } else { $_ }
}
if (-not $found) { $lines += "SeBatchLogonRight = *$sid" }
$lines | Set-Content $inf -Encoding Unicode
secedit /configure /db (Join-Path $env:TEMP 'rights.sdb') /cfg $inf /areas USER_RIGHTS | Out-Null

Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $work, "$work\results" | Out-Null
Copy-Item -Recurse $Payload "$work\payload"
Copy-Item -Recurse $Newer "$work\payload-newer"
Copy-Item -Recurse $Assets "$work\assets"
Copy-Item (Join-Path $PSScriptRoot 'windows_user.ps1') "$work\windows_user.ps1"
# The Gate 2 flow driver (#51) and the fixture helpers it imports, in the repository layout.
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
New-Item -ItemType Directory -Force "$work\flow\scripts\offline", "$work\flow\tests\fixtures" | Out-Null
Copy-Item "$repo\scripts\offline\flow_check.py" "$work\flow\scripts\offline\"
Copy-Item "$repo\tests\nca_helpers.py", "$repo\tests\publish_helpers.py", "$repo\tests\artifact_docs.py" "$work\flow\tests\"
Copy-Item "$repo\tests\fixtures\two_analyte.csv", "$repo\tests\fixtures\two_analyte.expected.json" "$work\flow\tests\fixtures\"
icacls $work /grant "${user}:(OI)(CI)M" /T /Q | Out-Null

function As-User([string]$leg, [hashtable]$envs = @{}) {
    $log = "$work\results\$leg.log"
    $cmd = "$work\$leg.cmd"
    $set = ($envs.GetEnumerator() | ForEach-Object { "set `"$($_.Key)=$($_.Value)`"" }) -join "`r`n"
    $body = "@echo off`r`n$set`r`n" +
        "%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe -NoProfile -ExecutionPolicy Bypass " +
        "-File $work\windows_user.ps1 -Leg $leg -Work $work > `"$log`" 2>&1`r`nexit /b %ERRORLEVEL%`r`n"
    [IO.File]::WriteAllText($cmd, $body, (New-Object Text.UTF8Encoding($false)))
    $name = "opncafyr-ci-$leg"
    Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
    $action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument "/c $cmd"
    $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 1) -AllowStartIfOnBatteries
    Register-ScheduledTask -TaskName $name -Action $action -User $user -Password $pw -RunLevel Limited -Settings $settings | Out-Null
    Start-ScheduledTask -TaskName $name | Out-Null
    $deadline = (Get-Date).AddMinutes(60)
    Start-Sleep -Seconds 3
    while ((Get-ScheduledTask -TaskName $name).State -eq 'Running' -and (Get-Date) -lt $deadline) { Start-Sleep -Seconds 5 }
    $rc = (Get-ScheduledTaskInfo -TaskName $name).LastTaskResult
    Unregister-ScheduledTask -TaskName $name -Confirm:$false | Out-Null
    Write-Host "::group::$leg (exit $rc)"
    if (Test-Path $log) { Get-Content $log | ForEach-Object { Write-Host $_ } }
    Write-Host '::endgroup::'
    Copy-Item $log $Results -ErrorAction SilentlyContinue
    return $rc
}

if ($SourceBuild) {
    foreach ($leg in 'identity', 'install', 'source', 'uninstall') {
        $rc = As-User $leg
        Copy-Item -Recurse "$work\results\*" $Results -ErrorAction SilentlyContinue
        if ($rc -ne 0) { Fail "leg $leg failed (exit $rc)" }
    }
    if ([Environment]::GetEnvironmentVariable('Path', 'Machine') -ne $machinePathBefore) { Fail 'the machine PATH changed' }
    Write-Host 'source build legs passed'
    exit 0
}
foreach ($leg in 'identity', 'install', 'noop', 'launch', 'failure', 'uninstall') {
    $rc = As-User $leg
    if ($rc -ne 0) { Copy-Item -Recurse "$work\results\*" $Results -ErrorAction SilentlyContinue; Fail "leg $leg failed (exit $rc)" }
}

# ---- #36: bundle builder (as the runner, online) ---------------------------------------------
$b1 = Join-Path $Results 'bundle-build-1'; $b2 = Join-Path $Results 'bundle-build-2'
foreach ($b in $b1, $b2) {
    New-Item -ItemType Directory -Force $b | Out-Null
    $env:OPNCAFYR_APP_ROOT = Join-Path $b 'cache'
    $extra = @(); if ($b -eq $b1) { $extra = @('--archive') }
    & "$Payload\install.bat" bundle --platform windows-x86_64 --out $b --assets $Assets @extra
    if ($LASTEXITCODE -ne 0) { Fail "bundle build failed in $b" }
}
Remove-Item Env:OPNCAFYR_APP_ROOT
$name = (Get-ChildItem $b1 -Directory -Filter 'opncafyr-bundle-*').Name
if ((Get-FileHash "$b1\$name\bundle.json").Hash -ne (Get-FileHash "$b2\$name\bundle.json").Hash) { Fail 'two builds of the bundle differ' }
Copy-Item "$b1\$name\bundle.json", "$b1\$name\licenses.json", "$b1\$name\sbom.spdx.json" $Results
New-Item -ItemType Directory -Force "$work\bundle" | Out-Null
Copy-Item "$b1\$name.zip" "$work\bundle\"
Copy-Item -Recurse "$b1\$name" "$work\bundle\$name"
# The zip is uploaded as a workflow artifact; the rest of the build folders go.
$artifact = Join-Path (Split-Path $Results) 'bundle-artifact'
New-Item -ItemType Directory -Force $artifact | Out-Null
Move-Item "$b1\$name.zip" $artifact
Remove-Item -Recurse -Force $b1, $b2
icacls $work /grant "${user}:(OI)(CI)M" /T /Q | Out-Null
[IO.File]::WriteAllText("$work\bundle\name.txt", $name)

# ---- #36: block the user's network, log drops ---------------------------------------------
$fwLog = Join-Path $env:SystemRoot 'System32\LogFiles\Firewall\pfirewall.log'
Set-NetFirewallProfile -All -Enabled True -LogBlocked True -LogMaxSizeKilobytes 32767 -LogFileName $fwLog
$rule = 'opncafyr_ci_offline'
New-NetFirewallRule -Name $rule -DisplayName $rule -Direction Outbound -Action Block -Profile Any `
    -LocalUser "O:LSD:(A;;CC;;;$sid)" `
    -RemoteAddress @('0.0.0.0-126.255.255.255', '128.0.0.0-255.255.255.255', '::2-ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff') | Out-Null
# Blocked connections are recorded as Security event 5157 (Windows Filtering Platform),
# with the process, direction and destination; the firewall's own drop log is a fallback.
auditpol /set /subcategory:"Filtering Platform Connection" /failure:enable | Out-Null
function Drops([datetime]$since) {
    $found = @()
    $events = Get-WinEvent -FilterHashtable @{ LogName = 'Security'; Id = 5157; StartTime = $since } -ErrorAction SilentlyContinue
    foreach ($e in $events) {
        $d = @{}
        ([xml]$e.ToXml()).Event.EventData.Data | ForEach-Object { $d[$_.Name] = $_.'#text' }
        # Outbound, not loopback, and not a Windows service (service-hardening rules block
        # some services' traffic on their own; the test user's processes aren't services).
        if ($d['Direction'] -eq '%%14593' -and $d['DestAddress'] -notmatch '^(127\.|::1$|0\.0\.0\.0$)' -and
            $d['Application'] -notmatch '\\(svchost|lsass|services)\.exe$' -and $d['Application'] -ne 'System') {
            $found += "5157 $($e.TimeCreated.ToString('s')) pid=$($d['ProcessID']) $($d['Application']) -> $($d['DestAddress']):$($d['DestPort'])"
        }
    }
    if (Test-Path $fwLog) {
        $found += @(Get-Content $fwLog -ErrorAction SilentlyContinue | Where-Object {
            $f = $_ -split ' '
            $f.Count -gt 16 -and $f[2] -eq 'DROP' -and $f[16] -eq 'SEND' -and
                ([datetime]"$($f[0]) $($f[1])") -ge $since.AddSeconds(-1) -and $f[5] -notmatch '^(127\.|::1$)'
        } | ForEach-Object { "fwlog $_" })
    }
    return $found
}
$t0 = Get-Date
if ((As-User 'sanity') -ne 0) { Fail 'leg sanity failed' }
Start-Sleep -Seconds 5
$seen = Drops $t0
Write-Host "sanity: $($seen.Count) dropped packets logged for a deliberate attempt"
if ($seen.Count -eq 0) { Fail 'the firewall log records no drops; the network check would prove nothing' }

$t1 = Get-Date
foreach ($leg in 'offline', 'flow', 'tamper', 'interrupted', 'update') {
    $rc = As-User $leg @{ OPNCAFYR_PROXY_TRAP = '0' }
    if ($rc -ne 0) { Copy-Item -Recurse "$work\results\*" $Results -ErrorAction SilentlyContinue; Fail "leg $leg failed (exit $rc)" }
}
Start-Sleep -Seconds 5
$drops = Drops $t1
$drops | Set-Content (Join-Path $Results 'network-attempts.log')
Remove-NetFirewallRule -Name $rule
if ($drops.Count -ne 0) { $drops | Select-Object -First 20 | ForEach-Object { Write-Host $_ }; Fail "$($drops.Count) connection attempts with the network blocked" }
Write-Host 'offline legs: no connection attempts'
if ((As-User 'uninstall') -ne 0) { Fail 'leg uninstall (after the update) failed' }

# ---- nothing changed outside the user's profile --------------------------------------------
if ([Environment]::GetEnvironmentVariable('Path', 'Machine') -ne $machinePathBefore) { Fail 'machine PATH changed' }
if ((reg query 'HKLM\SOFTWARE\R-core\R' /s 2>$null | Out-String) -ne $hklmRBefore) { Fail 'HKLM R-core changed' }
Copy-Item -Recurse "$work\results\*" $Results -ErrorAction SilentlyContinue
Write-Host 'installer CI: all legs passed'
