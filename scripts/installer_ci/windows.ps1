# CI (#19): installer v1 end to end on a Windows runner, as a new standard user.
#
#   powershell -File scripts\installer_ci\windows.ps1 -Payload <dir> -Assets <dir> -Results <dir>
#
# The runner (an administrator) only sets up: it creates a local standard user whose
# name contains a space, lets it run batch jobs, and starts each leg as a scheduled task
# under that user (-RunLevel Limited). Every installer step runs as that user.
# -Assets holds the private opnca-r files (manifest.json and the Windows binary), which
# the installer takes through --assets.
param(
    [Parameter(Mandatory = $true)][string]$Payload,
    [Parameter(Mandatory = $true)][string]$Assets,
    [Parameter(Mandatory = $true)][string]$Results
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
Copy-Item -Recurse $Assets "$work\assets"
Copy-Item (Join-Path $PSScriptRoot 'windows_user.ps1') "$work\windows_user.ps1"
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

foreach ($leg in 'identity', 'install', 'noop', 'launch', 'failure', 'uninstall') {
    $rc = As-User $leg
    if ($rc -ne 0) { Copy-Item -Recurse "$work\results\*" $Results -ErrorAction SilentlyContinue; Fail "leg $leg failed (exit $rc)" }
}

# ---- nothing changed outside the user's profile --------------------------------------------
if ([Environment]::GetEnvironmentVariable('Path', 'Machine') -ne $machinePathBefore) { Fail 'machine PATH changed' }
if ((reg query 'HKLM\SOFTWARE\R-core\R' /s 2>$null | Out-String) -ne $hklmRBefore) { Fail 'HKLM R-core changed' }
Copy-Item -Recurse "$work\results\*" $Results -ErrorAction SilentlyContinue
Write-Host 'installer CI: all legs passed'
