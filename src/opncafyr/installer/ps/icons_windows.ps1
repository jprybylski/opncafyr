# Create or remove the opncafyr shortcuts (#19). Run by the installer:
#
#   powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File icons_windows.ps1
#
# Inputs come from environment variables (Windows PowerShell 5.1 drops embedded quotes in
# native arguments):
#   OPNCAFYR_ICON_ACTION       create | remove
#   OPNCAFYR_ICON_DIRS         ;-separated folders; "<Desktop>" means the user's desktop
#                              (OneDrive-redirected or not, from WScript.Shell)
#   OPNCAFYR_ICON_TARGET       pythonw.exe of the active install (create)
#   OPNCAFYR_ICON_ARGUMENTS    "-m opncafyr launch" (create)
#   OPNCAFYR_ICON_WORKDIR      working folder (create)
#   OPNCAFYR_ICON_DESCRIPTION  tooltip (create)
# Creating a .lnk through WScript.Shell is plain file I/O: no elevation, no registry.
$ErrorActionPreference = 'Stop'
$shell = New-Object -ComObject WScript.Shell
$name = 'opncafyr.lnk'
foreach ($dir in ($env:OPNCAFYR_ICON_DIRS -split ';' | Where-Object { $_ })) {
    if ($dir -eq '<Desktop>') { $dir = $shell.SpecialFolders.Item('Desktop') }
    $path = Join-Path $dir $name
    if ($env:OPNCAFYR_ICON_ACTION -eq 'remove') {
        if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Force; Write-Output "removed $path" }
        continue
    }
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    $lnk = $shell.CreateShortcut($path)
    $lnk.TargetPath = $env:OPNCAFYR_ICON_TARGET
    $lnk.Arguments = $env:OPNCAFYR_ICON_ARGUMENTS
    $lnk.WorkingDirectory = $env:OPNCAFYR_ICON_WORKDIR
    $lnk.Description = $env:OPNCAFYR_ICON_DESCRIPTION
    $lnk.IconLocation = "$($env:OPNCAFYR_ICON_TARGET),0"
    $lnk.Save()
    $check = $shell.CreateShortcut($path)
    if ($check.TargetPath -ne $env:OPNCAFYR_ICON_TARGET) { throw "shortcut $path points at $($check.TargetPath)" }
    Write-Output "created $path -> $($check.TargetPath) $($check.Arguments)"
}
