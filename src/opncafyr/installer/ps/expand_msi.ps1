# Unpack an MSI into a folder without the Windows Installer service (spike #8; used by the installer, #19).
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File expand_msi.ps1 -Msi <file.msi> -Dest <dir>
#
# `msiexec /a` needs the Windows Installer service, which refuses a standard user that
# isn't logged on interactively (0x80070005 over SSH, likewise for scheduled or remote
# jobs). This reads the package in-process instead: msi.dll's database API (no service)
# reads the embedded cabinets, expand.exe unpacks them, and the File, Component and
# Directory tables put each file where an administrative install would. No registry,
# no shortcuts, no custom actions.
param(
    [Parameter(Mandatory = $true)][string]$Msi,
    [Parameter(Mandatory = $true)][string]$Dest
)
$ErrorActionPreference = 'Stop'
$Msi = (Resolve-Path $Msi).Path
New-Item -ItemType Directory -Force $Dest | Out-Null
$Dest = (Resolve-Path $Dest).Path
$work = Join-Path $Dest '.msi-extract'
New-Item -ItemType Directory -Force $work | Out-Null

$installer = New-Object -ComObject WindowsInstaller.Installer
function Invoke($obj, [string]$name, [string]$kind, $argv) { $obj.GetType().InvokeMember($name, $kind, $null, $obj, $argv) }
$db = Invoke $installer 'OpenDatabase' 'InvokeMethod' @($Msi, 0)

function Rows([string]$sql, [int]$cols) {
    $view = Invoke $db 'OpenView' 'InvokeMethod' @($sql)
    Invoke $view 'Execute' 'InvokeMethod' $null | Out-Null
    while ($true) {
        $rec = Invoke $view 'Fetch' 'InvokeMethod' $null
        if ($null -eq $rec) { break }
        , @(1..$cols | ForEach-Object { Invoke $rec 'StringData' 'GetProperty' ([object[]]@([int]$_)) })
    }
    Invoke $view 'Close' 'InvokeMethod' $null | Out-Null
}

function Long([string]$name) { if ($name -match '\|') { $name.Split('|')[1] } else { $name } }

# Directory tree. DefaultDir is "target[:source]", each "short|long"; "." = parent.
$dirs = @{}
foreach ($r in Rows 'SELECT Directory, Directory_Parent, DefaultDir FROM Directory' 3) {
    $dirs[$r[0]] = @{ Parent = $r[1]; Name = Long ($r[2].Split(':')[0]) }
}
$resolved = @{}
function DirPath([string]$id) {
    if ($resolved.ContainsKey($id)) { return $resolved[$id] }
    $d = $dirs[$id]
    $p = if (-not $d.Parent -or $d.Parent -eq $id) { $Dest }
         else {
             $parent = DirPath $d.Parent
             if ($d.Name -eq '.' -or $d.Name -eq 'SourceDir') { $parent } else { Join-Path $parent $d.Name }
         }
    $resolved[$id] = $p
    $p
}
$compDir = @{}
foreach ($r in Rows 'SELECT Component, Directory_ FROM Component' 2) { $compDir[$r[0]] = $r[1] }
$files = @(Rows 'SELECT File, Component_, FileName FROM File' 3)

# Embedded cabinets: read each "#name" stream with msi.dll directly (the automation
# layer can't hand binary streams back) and unpack it with expand.exe.
Add-Type -TypeDefinition @"
using System;
using System.IO;
using System.Runtime.InteropServices;
public static class MsiStream {
    [DllImport("msi.dll", CharSet = CharSet.Unicode)] static extern uint MsiOpenDatabase(string path, IntPtr persist, out IntPtr db);
    [DllImport("msi.dll", CharSet = CharSet.Unicode)] static extern uint MsiDatabaseOpenView(IntPtr db, string sql, out IntPtr view);
    [DllImport("msi.dll")] static extern uint MsiViewExecute(IntPtr view, IntPtr rec);
    [DllImport("msi.dll")] static extern uint MsiViewFetch(IntPtr view, out IntPtr rec);
    [DllImport("msi.dll")] static extern uint MsiRecordReadStream(IntPtr rec, uint field, byte[] buf, ref uint size);
    [DllImport("msi.dll")] static extern uint MsiCloseHandle(IntPtr h);
    public static void Save(string msi, string name, string dest) {
        IntPtr db, view, rec;
        Check(MsiOpenDatabase(msi, IntPtr.Zero, out db));
        try {
            Check(MsiDatabaseOpenView(db, "SELECT `Data` FROM `_Streams` WHERE `Name` = '" + name.Replace("'", "") + "'", out view));
            Check(MsiViewExecute(view, IntPtr.Zero));
            Check(MsiViewFetch(view, out rec));
            byte[] buf = new byte[1 << 20];
            using (FileStream fs = File.Create(dest)) {
                while (true) {
                    uint n = (uint)buf.Length;
                    Check(MsiRecordReadStream(rec, 1, buf, ref n));
                    if (n == 0) break;
                    fs.Write(buf, 0, (int)n);
                }
            }
            MsiCloseHandle(rec); MsiCloseHandle(view);
        } finally { MsiCloseHandle(db); }
    }
    static void Check(uint rc) { if (rc != 0) throw new Exception("msi.dll error " + rc); }
}
"@
$cabs = @(Rows 'SELECT Cabinet FROM Media' 1 | ForEach-Object { $_[0] } | Where-Object { $_ -like '#*' } | ForEach-Object { $_.Substring(1) })
$flat = Join-Path $work 'flat'
New-Item -ItemType Directory -Force $flat | Out-Null
[System.Runtime.InteropServices.Marshal]::ReleaseComObject($db) | Out-Null
foreach ($cab in $cabs) {
    $cabFile = Join-Path $work $cab
    [MsiStream]::Save($Msi, $cab, $cabFile)
    & expand.exe -F:* $cabFile $flat | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "expand.exe failed on $cab" }
    Remove-Item $cabFile
}

# Place each file: the cabinet names it by its File key.
$n = 0
foreach ($r in $files) {
    $src = Join-Path $flat $r[0]
    if (-not (Test-Path -LiteralPath $src)) { throw "file $($r[0]) not found in the cabinets" }
    $dir = DirPath $compDir[$r[1]]
    New-Item -ItemType Directory -Force $dir | Out-Null
    Move-Item -LiteralPath $src -Destination (Join-Path $dir (Long $r[2])) -Force
    $n++
}
Remove-Item -Recurse -Force $work
"$n files from $($cabs.Count) cabinet(s)"
