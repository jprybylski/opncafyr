# CI (#19, #36): one installer leg, run as the standard user by windows.ps1 (scheduled task).
#   powershell -File windows_user.ps1 -Leg <leg> -Work <dir>
#   #19 legs: identity, install, noop, launch, failure, uninstall
#   #36 legs: sanity, offline, tamper, interrupted, update
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
$bundleName = if (Test-Path "$Work\bundle\name.txt") { (Get-Content "$Work\bundle\name.txt" -Raw).Trim() } else { '' }
$bundleZip = "$Work\bundle\$bundleName.zip"
$bundleDir = "$Work\bundle\$bundleName"

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
    'sanity' {
        # With the firewall rule on, this must fail and be logged as a drop.
        $curl = Join-Path $env:SystemRoot 'System32\curl.exe'
        $rc = Native $curl @('-sS', '-m', '10', '-o', 'NUL', 'https://example.com')
        if ($rc -eq 0) { Fail 'the network is not blocked' }
    }
    'offline' {
        $rc = Native "$Work\payload\install.bat" @('install', '--from-bundle', $bundleZip)
        if ($rc -ne 0) { Fail "offline install exited $rc" }
        if ($script:lastOut -notmatch '\(bundle\)') { Fail 'not a bundle install' }
        $report = Get-ChildItem (Join-Path $root 'state\reports') -Filter 'install-report-*.json' | Sort-Object LastWriteTime | Select-Object -Last 1
        Copy-Item $report.FullName "$Work\results\report-offline.json"
        Copy-Item ($report.FullName -replace '\.json$', '.html') "$Work\results\report-offline.html"
        $r = Get-Content $report.FullName -Raw | ConvertFrom-Json
        $manifest = Get-Content (Join-Path $bundleDir 'bundle.json') -Raw | ConvertFrom-Json
        if ($r.result -ne 'pass') { Fail "report result $($r.result)" }
        if ($r.source -ne 'bundle' -or $r.bundle.bundle_id -ne $manifest.bundle_id) { Fail 'report does not name the bundle' }
        foreach ($n in 'opnca_calculation', 'quarto_docx_render', 'deckifyr_build') {
            if (-not ($r.smoke | Where-Object { $_.name -eq $n -and $_.status -eq 'ok' })) { Fail "report lacks a passing $n" }
        }
        Health 47813
    }
    'tamper' {
        $before = Active
        $t = "$Work\tampered"
        Remove-Item -Recurse -Force $t -ErrorAction SilentlyContinue
        Copy-Item -Recurse $bundleDir $t
        $victim = Get-ChildItem "$t\assets\quarto" | Select-Object -First 1
        [IO.File]::AppendAllText($victim.FullName, 'x')
        Remove-Item -Recurse -Force (Join-Path $root 'cache\downloads') -ErrorAction SilentlyContinue
        $rc = Native "$Work\payload-newer\install.bat" @('install', '--from-bundle', $t)
        if ($rc -ne 1) { Fail "a tampered bundle was not refused (exit $rc)" }
        if ($script:lastOut -notmatch 'in: quarto') { Fail 'the error does not name quarto' }
        if ((Active) -ne $before) { Fail 'the active install changed' }
        Health 47814
    }
    'interrupted' {
        $before = Active
        $log = "$Work\results\interrupted-run.log"
        $p = Start-Process -FilePath (Join-Path $env:SystemRoot 'System32\cmd.exe') -PassThru -WindowStyle Hidden `
            -ArgumentList "/c `"`"$Work\payload-newer\install.bat`" install --from-bundle `"$bundleZip`" > `"$log`" 2>&1`""
        $reached = $false
        for ($i = 0; $i -lt 600 -and -not $p.HasExited; $i++) {
            Start-Sleep -Seconds 1
            if ((Test-Path $log) -and (Select-String -Path $log -Pattern '^\[r_library\]' -Quiet)) { $reached = $true; break }
        }
        $ErrorActionPreference = 'Continue'; & taskkill.exe /T /F /PID $p.Id *> $null; $ErrorActionPreference = 'Stop'
        Start-Sleep -Seconds 3
        if (-not $reached) { Fail 'the update never reached r_library' }
        if ((Active) -ne $before) { Fail 'the active install changed after an interrupted update' }
        Health 47815
    }
    'update' {
        $rc = Native "$Work\payload-newer\install.bat" @('install', '--from-bundle', $bundleZip)
        if ($rc -ne 0) { Fail "offline update exited $rc" }
        if ($script:lastOut -notmatch 'removing incomplete install') { Fail 'the interrupted install was not cleaned up' }
        $newer = (Get-Content "$Work\payload-newer\payload.json" -Raw | ConvertFrom-Json).version
        if ((Active) -notlike "$newer-*") { Fail "the update is not active: $(Active)" }
        Health 47816
    }
    default { Fail "unknown leg $Leg" }
}
Write-Output "leg $Leg ok"
exit 0
