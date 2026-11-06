# opncafyr per-user installer for Windows (#19, #36). No administrator rights.
#
#   install.bat [install]  [--from-bundle DIR|ZIP] [--assets DIR] [--with-libreoffice] [--dry-run]
#   install.bat repair     (same options)
#   install.bat uninstall  [--force] [--dry-run]
#   install.bat status
#
# This wrapper only finds the pinned python-build-standalone archive (in the download
# cache, the bundle, an --assets folder, or by downloading it from its pinned URL unless
# --from-bundle is given), checks its SHA-256 from bootstrap.tsv, unpacks it under
# <app root>\cache\bootstrap\ and runs run_installer.py with it. Everything else is
# opncafyr.installer (docs/install.md).
#
# Windows PowerShell 5.1: native tools are judged by exit code only (their stderr is not
# an error), and arguments are forwarded unchanged with @args.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$sys32 = Join-Path $env:SystemRoot 'System32'
$tar = Join-Path $sys32 'tar.exe'
$curl = Join-Path $sys32 'curl.exe'

if (-not [Environment]::Is64BitOperatingSystem) { Write-Error 'opncafyr needs 64-bit Windows 11.'; exit 1 }
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Warning 'Running elevated is not needed: opncafyr installs for the current user only.'
}

$root = if ($env:OPNCAFYR_APP_ROOT) { $env:OPNCAFYR_APP_ROOT } else { Join-Path $env:LOCALAPPDATA 'opncafyr' }
$line = Get-Content -LiteralPath (Join-Path $here 'bootstrap.tsv') | Where-Object { $_.Split("`t")[0] -eq 'windows-x86_64' } | Select-Object -First 1
if (-not $line) { Write-Error 'bootstrap.tsv has no Python for windows-x86_64'; exit 1 }
$f = $line.Split("`t")
$file = $f[1]; $sha = $f[2]; $url = $f[3]

$bundle = $null
$sources = @()
for ($i = 0; $i -lt $args.Count; $i++) {
    $a = [string]$args[$i]
    if ($a -eq '--from-bundle' -and $i + 1 -lt $args.Count) { $bundle = [string]$args[$i + 1]; $sources += $bundle }
    elseif ($a -like '--from-bundle=*') { $bundle = $a.Substring(14); $sources += $bundle }
    elseif ($a -eq '--assets' -and $i + 1 -lt $args.Count) { $sources += [string]$args[$i + 1] }
    elseif ($a -like '--assets=*') { $sources += $a.Substring(9) }
}

function Test-Sha([string]$path) {
    (Test-Path -LiteralPath $path -PathType Leaf) -and ((Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLower() -eq $sha)
}
function Invoke-Native([string]$exe, [string[]]$argv) {
    # Output goes straight to the console (Out-Host), so only the exit code is returned.
    $eap = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    try { & $exe @argv | Out-Host } finally { $ErrorActionPreference = $eap }
    return $LASTEXITCODE
}

$boot = Join-Path $root "cache\bootstrap\$sha"
$archive = Join-Path $boot $file
$part = "$archive.part"
if (-not (Test-Sha $archive)) {
    New-Item -ItemType Directory -Force -Path $boot | Out-Null
    Remove-Item -LiteralPath $part -Force -ErrorAction SilentlyContinue
    foreach ($src in $sources) {
        if (Test-Path -LiteralPath $src -PathType Container) {
            foreach ($c in @((Join-Path $src "assets\python\$file"), (Join-Path $src $file))) {
                if (Test-Sha $c) { Copy-Item -LiteralPath $c -Destination $part; break }
            }
        } elseif (Test-Path -LiteralPath $src -PathType Leaf) {
            $tmp = Join-Path $boot 'from-zip'
            Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
            New-Item -ItemType Directory -Force -Path $tmp | Out-Null
            $member = (& $tar -tf $src) | Where-Object { $_ -like "*assets/python/$file" } | Select-Object -First 1
            if ($member -and (Invoke-Native $tar @('-xf', $src, '-C', $tmp, $member)) -eq 0) {
                $got = Join-Path $tmp ($member -replace '/', '\')
                if (Test-Sha $got) { Move-Item -LiteralPath $got -Destination $part }
            }
            Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
        }
        if (Test-Path -LiteralPath $part) { break }
    }
    if (-not (Test-Path -LiteralPath $part)) {
        if ($bundle) { Write-Error "python: $file is missing from the bundle (or doesn't match its pinned SHA-256)."; exit 1 }
        Write-Host "downloading the pinned Python ($file)"
        $rc = Invoke-Native $curl @('--silent', '--show-error', '--fail', '--location', '--proto', '=https', '--proto-redir', '=https',
            '--retry', '3', '--connect-timeout', '30', '--ssl-revoke-best-effort', '--output', $part, $url)
        if ($rc -ne 0) { Write-Error "download failed (curl exit $rc): $url"; exit 1 }
    }
    if (-not (Test-Sha $part)) { Remove-Item -LiteralPath $part -Force; Write-Error "python: $file does not match its pinned SHA-256."; exit 1 }
    Move-Item -LiteralPath $part -Destination $archive -Force
}

$pyDir = Join-Path $boot 'python'
$py = Join-Path $pyDir 'python.exe'
if (-not (Test-Path -LiteralPath $py)) {
    $tmp = Join-Path $boot 'python.tmp'
    Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path $tmp | Out-Null
    if ((Invoke-Native $tar @('-xzf', $archive, '-C', $tmp)) -ne 0) { Write-Error "could not unpack $file"; exit 1 }
    Move-Item -LiteralPath (Join-Path $tmp 'python') -Destination $pyDir
    Remove-Item -LiteralPath $tmp -Recurse -Force
}
if ((Invoke-Native $py @('-I', '-c', 'import sys')) -ne 0) {
    Write-Error "The bundled Python can't run here. If your organisation blocks programs in $root, ask IT to allow it (see docs/install.md)."
    exit 1
}

$forward = @($args)
if ($forward.Count -eq 0) { $forward = @('install') }
$runner = Join-Path $here 'run_installer.py'
if ($forward[0] -eq 'uninstall') {
    # Files in use can't be deleted on Windows: run the uninstaller from a temporary copy
    # of Python and of this payload, outside the app root it removes. When this script
    # runs from the installed copy (<app root>\installer), cmd still has to read the rest
    # of install.bat from that folder, so the uninstaller leaves it and a detached cmd
    # removes the app root a few seconds after this window has finished.
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ('opncafyr-uninstall-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Force -Path $tmp | Out-Null
    Copy-Item -LiteralPath $pyDir -Destination (Join-Path $tmp 'python') -Recurse
    Copy-Item -LiteralPath $here -Destination (Join-Path $tmp 'payload') -Recurse
    $py = Join-Path $tmp 'python\python.exe'
    $runner = Join-Path $tmp 'payload\run_installer.py'
    $inside = $here.TrimEnd('\').StartsWith($root.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)
    if ($inside) { $forward += @('--leave', $here) }
    Push-Location $env:TEMP
    try { $rc = Invoke-Native $py (@('-I', '-B', $runner) + $forward) } finally { Pop-Location }
    Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
    if ($inside -and $rc -eq 0 -and -not ($forward -contains '--dry-run')) {
        $cmd = Join-Path $sys32 'cmd.exe'
        Start-Process -FilePath $cmd -WindowStyle Hidden -WorkingDirectory $env:TEMP `
            -ArgumentList "/c ping -n 4 127.0.0.1 >nul & rd /s /q `"$root`""
    }
    exit $rc
}
$rc = Invoke-Native $py (@('-I', '-B', $runner) + $forward)
exit $rc
