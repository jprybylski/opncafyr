# CI (#19): one installer leg, run as the standard user by windows.ps1 (scheduled task).
#   powershell -File windows_user.ps1 -Leg <identity|install|noop|launch|failure|uninstall> -Work <dir>
param(
    [Parameter(Mandatory = $true)][string]$Leg,
    [Parameter(Mandatory = $true)][string]$Work
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$installer = "$Work\payload\install.bat"
$root = Join-Path $env:LOCALAPPDATA 'opncafyr'
$shell = New-Object -ComObject WScript.Shell
$desktopLnk = Join-Path $shell.SpecialFolders.Item('Desktop') 'opncafyr.lnk'
$startLnk = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\opncafyr.lnk'

function Fail([string]$msg) { Write-Output "FAIL: $msg"; exit 1 }
function Native([string]$exe, [string[]]$argv) {
    # Output goes to the host (the leg's log); only the exit code is returned.
    $eap = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    try { $out = @(& $exe @argv 2>&1 | ForEach-Object { "$_" }) } finally { $ErrorActionPreference = $eap }
    $code = $LASTEXITCODE
    $out | ForEach-Object { Write-Host $_ }
    $script:lastOut = ($out -join "`n")
    return $code
}
function Active { $p = Join-Path $root 'current'; if (Test-Path $p) { (Get-Content $p -Raw).Trim() } else { '' } }
function Health([int]$port) {
    $lnk = $shell.CreateShortcut($desktopLnk)
    Write-Output "shortcut: $($lnk.TargetPath) $($lnk.Arguments)"
    if (-not (Test-Path $lnk.TargetPath)) { Fail "shortcut target missing: $($lnk.TargetPath)" }
    # The shortcut's own target (pythonw.exe, no console), with test flags.
    $p = Start-Process -FilePath $lnk.TargetPath -ArgumentList "$($lnk.Arguments) --no-browser --port $port" -PassThru
    $body = $null
    for ($i = 0; $i -lt 60 -and -not $body; $i++) {
        Start-Sleep -Seconds 1
        try { $body = (Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:$port/health" -TimeoutSec 2).Content } catch { }
    }
    $ErrorActionPreference = 'Continue'; & taskkill.exe /T /F /PID $p.Id *> $null; $ErrorActionPreference = 'Stop'
    Write-Output "health: $body"
    if (-not $body -or $body -notmatch '"status":"ok"') { Fail 'launcher did not answer /health' }
}

switch ($Leg) {
    'identity' {
        $groups = whoami /groups | Out-String
        Write-Output (whoami); Write-Output $groups
        if ($groups -notmatch 'Mandatory Label\\Medium Mandatory Level') { Fail 'not Medium integrity' }
        if ($groups -match 'BUILTIN\\Administrators\s') { Fail 'member of Administrators' }
        Write-Output "LOCALAPPDATA=$env:LOCALAPPDATA"
    }
    'install' {
        $rc = Native $installer @('install', '--assets', "$Work\assets")
        if ($rc -ne 0) { Fail "install exited $rc" }
        if (-not (Active)) { Fail 'nothing is active' }
        foreach ($l in $desktopLnk, $startLnk) { if (-not (Test-Path $l)) { Fail "no shortcut $l" } }
        # R's per-user installer must leave no registration behind (r_registry).
        if (Test-Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\R for Windows 4.6.2_is1') { Fail 'R uninstall entry left in HKCU' }
        if (Test-Path 'HKCU:\Software\R-core') { Fail 'HKCU R-core was written' }
        Native $installer @('status') | Out-Null
    }
    'noop' {
        $before = Active
        $rc = Native $installer @('install', '--assets', "$Work\assets")
        if ($rc -ne 0) { Fail "second run exited $rc" }
        if ($script:lastOut -notmatch 'is already installed') { Fail 'second run was not a no-op' }
        if ((Active) -ne $before) { Fail 'second run changed the active install' }
    }
    'launch' { Health 47811 }
    'failure' {
        $before = Active
        $env:OPNCAFYR_INSTALL_FAIL_AFTER = 'staging'
        $rc = Native $installer @('install', '--reinstall', '--assets', "$Work\assets")
        Remove-Item Env:OPNCAFYR_INSTALL_FAIL_AFTER
        if ($rc -ne 1) { Fail "injected failure exited $rc, not 1" }
        if ((Active) -ne $before) { Fail 'the active install changed after a failed install' }
        if ($script:lastOut -notmatch 'Previous install still active') { Fail 'no rollback message' }
        $zips = Get-ChildItem (Join-Path $root 'logs\support') -Filter 'install-failure-*.zip'
        if (-not $zips) { Fail 'no support bundle' }
        Copy-Item $zips.FullName "$Work\results\"
        Health 47812
    }
    'uninstall' {
        $study = Join-Path $env:USERPROFILE 'study'
        New-Item -ItemType Directory -Force (Join-Path $study '.opncafyr') | Out-Null
        [IO.File]::WriteAllText((Join-Path $study 'data.csv'), "a,b`n1,2`n")
        [IO.File]::WriteAllText((Join-Path $study '.opncafyr\workspace.json'), '{}')
        Copy-Item -Recurse (Join-Path $root 'logs') "$Work\results\app-logs" -ErrorAction SilentlyContinue
        $rc = Native (Join-Path $root 'installer\install.bat') @('uninstall')
        if ($rc -ne 0) { Fail "uninstall exited $rc" }
        # The installer folder (where install.bat ran) is removed by a detached cmd.
        for ($i = 0; $i -lt 30 -and (Test-Path $root); $i++) { Start-Sleep -Seconds 1 }
        if (Test-Path $root) { Fail "app root still exists: $(Get-ChildItem -Recurse $root | Select-Object -First 5 | Out-String)" }
        foreach ($l in $desktopLnk, $startLnk) { if (Test-Path $l) { Fail "shortcut left: $l" } }
        if (-not (Test-Path (Join-Path $study 'data.csv'))) { Fail 'the workspace was touched' }
        if (-not (Test-Path (Join-Path $study '.opncafyr\workspace.json'))) { Fail 'the workspace metadata was touched' }
    }
    default { Fail "unknown leg $Leg" }
}
Write-Output "leg $Leg ok"
exit 0
