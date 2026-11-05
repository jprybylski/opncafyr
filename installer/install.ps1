# opncafyr per-user installer for Windows (stub).
#
# Runs without elevation. Today it only performs preflight detection and exits.
# The full flow (Gate 1/4): download pinned assets from runtime/pins.toml, verify
# SHA-256, install into a staging dir, restore locks, smoke-test, create a desktop
# .lnk, then atomically activate. Modes: install | repair | uninstall.
param([ValidateSet('install', 'repair', 'uninstall')][string]$Mode = 'install')
$ErrorActionPreference = 'Stop'

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Warning 'Running elevated is not needed; opncafyr installs for the current user only.'
}

$appRoot = Join-Path $env:LOCALAPPDATA 'opncafyr'
Write-Host "opncafyr installer ($Mode) - preflight"
Write-Host "  os:        $([Environment]::OSVersion.VersionString)"
Write-Host "  arch:      $env:PROCESSOR_ARCHITECTURE"
Write-Host "  app root:  $appRoot"

$drive = Get-PSDrive -Name ($env:LOCALAPPDATA.Substring(0, 1))
Write-Host ("  free disk: {0:N0} MiB" -f ($drive.Free / 1MB))
try {
    $probe = Join-Path $env:LOCALAPPDATA ('.opncafyr-probe-' + [guid]::NewGuid())
    New-Item -ItemType File -Path $probe | Out-Null; Remove-Item $probe
    Write-Host '  writable:  yes'
} catch { Write-Host '  writable:  NO' }

Write-Host ''
Write-Error 'Installation is not implemented yet (see docs/plan.md, Gate 1 installer issue).' -ErrorAction Continue
exit 2
