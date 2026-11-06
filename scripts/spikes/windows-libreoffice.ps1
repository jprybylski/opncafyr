# Spike #8 (Windows half): per-user LibreOffice without admin, and how reliable it is for
# deckifyr previews and quartifyr field recalculation.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File windows-libreoffice.ps1 `
#       -Msi <LibreOffice_x.y.z_Win_x86-64.msi> -Sha256 <pinned> [-Runs 20] [-Parallel 4]
#
# Needs a finished windows-install.ps1 run under the same -Root (tools venv, R library with
# quartifyr, the built deck in work\deck and the rendered report in work\report).
# Writes <Root>\logs\libreoffice.json.
param(
    [string]$Root = (Join-Path $env:LOCALAPPDATA 'opncafyr\spike'),
    [Parameter(Mandatory = $true)][string]$Msi,
    [Parameter(Mandatory = $true)][string]$Sha256,
    [int]$Runs = 20,
    [int]$Parallel = 4,
    [int]$TimeoutSec = 180
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$Logs = Join-Path $Root 'logs'
$Work = Join-Path $Root 'work\lo'
$Lo = Join-Path $Root 'libreoffice'
$VenvPy = Join-Path $Root 'tools-venv\Scripts\python.exe'
$Lib = Join-Path $Root 'rlib'
$result = [ordered]@{}

if ((Get-FileHash -Algorithm SHA256 $Msi).Hash.ToLower() -ne $Sha256) { throw 'MSI SHA-256 mismatch' }

# 1. Unpack the MSI per user. `msiexec /a` (administrative install) is tried first and
#    recorded: in a desktop session it works, but over SSH (or any non-interactive logon)
#    the Windows Installer service refuses a standard user (1601, 0x80070005). The files
#    are always unpacked in-process by expand_msi.ps1, which works in both cases.
if (Test-Path $Lo) { Remove-Item -Recurse -Force $Lo }
$p = Start-Process msiexec.exe -Wait -PassThru -ArgumentList @('/a', "`"$Msi`"", '/qn', "TARGETDIR=`"$Lo-msiexec`"", '/L*v', "`"$Logs\lo-msi.log`"")
$result.msiexec_admin_exit = $p.ExitCode
if (Test-Path "$Lo-msiexec") { Remove-Item -Recurse -Force "$Lo-msiexec" }
$t0 = Get-Date
$result.extract = (& (Join-Path $PSScriptRoot 'expand_msi.ps1') -Msi $Msi -Dest $Lo) -join ' '
$result.extract_seconds = [math]::Round(((Get-Date) - $t0).TotalSeconds, 1)
$program = Join-Path $Lo 'LibreOffice\program'
# The MSI would put the VC++ 2015-2022 runtime into System32 (admin). Copying those DLLs
# next to soffice.exe instead breaks LibreOffice (soffice --version prints nothing), so a
# per-user copy relies on the runtime already being installed system-wide.
$result.system_vcruntime = (Test-Path "$env:windir\System32\vcruntime140_1.dll") -and (Test-Path "$env:windir\System32\msvcp140.dll")
# Bundled fonts go into LibreOffice's own fonts folder instead of the system's.
New-Item -ItemType Directory -Force (Join-Path $Lo 'LibreOffice\share\fonts\truetype') | Out-Null
Copy-Item (Join-Path $Lo 'Fonts\*') (Join-Path $Lo 'LibreOffice\share\fonts\truetype') -Force
Remove-Item -Recurse -Force (Join-Path $Lo 'System'), (Join-Path $Lo 'System64'), (Join-Path $Lo 'Fonts')
$soffice = Join-Path $program 'soffice.exe'
if (-not (Test-Path $soffice)) { throw 'no soffice.exe after unpacking' }
$result.size_mb = [math]::Round((Get-ChildItem $Lo -Recurse -File | Measure-Object Length -Sum).Sum / 1MB)
$result.files = (Get-ChildItem $Lo -Recurse -File).Count

New-Item -ItemType Directory -Force $Work | Out-Null
$deckSrc = Join-Path $Root 'work\deck'
$docxSrc = Get-Item (Join-Path $Root 'work\report\report\draft\report-draft.docx')

function Profile-Url([string]$name) { 'file:///' + ((Join-Path $Work "profile-$name") -replace '\\', '/') }

# 2. First run (fresh profile) and a warm run, converting the deck to PDF.
foreach ($run in 'first', 'warm') {
    $out = Join-Path $Work "pdf-$run"
    $t0 = Get-Date
    $p = Start-Process $soffice -Wait -PassThru -WindowStyle Hidden -ArgumentList @(
        "-env:UserInstallation=$(Profile-Url 'convert')", '--headless', '--norestore', '--convert-to', 'pdf',
        '--outdir', "`"$out`"", "`"$deckSrc\build\deck.pptx`"")
    $result["convert_$($run)_seconds"] = [math]::Round(((Get-Date) - $t0).TotalSeconds, 1)
    $result["convert_$($run)_ok"] = ($p.ExitCode -eq 0) -and (Test-Path "$out\deck.pdf")
}

# 3. Repeated jobs. Each job runs in its own copy so concurrent jobs never share files.
function Start-Job-Process([string]$kind, [int]$i) {
    $dir = Join-Path $Work "$kind-$i"
    if (Test-Path $dir) { Remove-Item -Recurse -Force $dir }
    if ($kind -eq 'preview') {
        Copy-Item -Recurse $deckSrc $dir
        $argv = @('-m', 'deckifyr', '--json', 'preview', "`"$dir\presentation.yaml`"", '--trusted-binaries-only', "--soffice-bin=`"$soffice`"")
        $env:PYTHONPATH = ''
        $env:PATH = $script:basePath
    } else {
        New-Item -ItemType Directory -Force $dir | Out-Null
        Copy-Item $docxSrc.FullName (Join-Path $dir 'report.docx')
        $argv = @('-m', 'quartifyr_styling', 'recalculate-fields', '--docx', "`"$dir\report.docx`"", '--timeout', '150')
        # quartifyr finds soffice on PATH only (no override), so the job's PATH leads with it.
        $env:PYTHONPATH = Join-Path $Lib 'quartifyr\python'
        $env:PATH = (Split-Path $soffice) + ';' + $script:basePath
    }
    $p = Start-Process $VenvPy -PassThru -WindowStyle Hidden -ArgumentList $argv `
        -RedirectStandardOutput "$dir.out" -RedirectStandardError "$dir.err"
    [pscustomobject]@{ Kind = $kind; I = $i; Proc = $p; Start = Get-Date; Dir = $dir }
}

function Wait-Jobs($jobs) {
    foreach ($j in $jobs) {
        $done = $j.Proc.WaitForExit($TimeoutSec * 1000)
        $secs = [math]::Round(((Get-Date) - $j.Start).TotalSeconds, 1)
        if (-not $done) { & taskkill.exe /T /F /PID $j.Proc.Id *> $null }
        $ok = $done -and $j.Proc.ExitCode -eq 0
        if ($ok -and $j.Kind -eq 'preview') { $ok = @(Get-ChildItem $j.Dir -Recurse -Filter *.png).Count -gt 0 }
        [pscustomobject]@{ kind = $j.Kind; i = $j.I; seconds = $secs; hung = -not $done; ok = $ok; exit = if ($done) { $j.Proc.ExitCode } else { $null } }
    }
}

$script:basePath = $env:PATH
$all = @()
foreach ($kind in 'preview', 'recalc') {
    for ($i = 1; $i -le $Runs; $i++) { $all += Wait-Jobs @(Start-Job-Process $kind $i) | ForEach-Object { $_ | Add-Member mode 'sequential' -PassThru } }
    for ($i = 1; $i -le $Runs; $i += $Parallel) {
        $batch = @(); for ($k = $i; $k -lt $i + $Parallel -and $k -le $Runs; $k++) { $batch += Start-Job-Process $kind (100 + $k) }
        $all += Wait-Jobs $batch | ForEach-Object { $_ | Add-Member mode "concurrent-$Parallel" -PassThru }
    }
}
$env:PATH = $script:basePath
$result.runs = $all | Group-Object kind, mode | ForEach-Object {
    $g = $_.Group
    [ordered]@{ kind = $g[0].kind; mode = $g[0].mode; runs = $g.Count; ok = @($g | Where-Object ok).Count;
        hung = @($g | Where-Object hung).Count; median_s = ($g.seconds | Sort-Object)[[int][math]::Floor($g.Count / 2)];
        max_s = ($g.seconds | Measure-Object -Maximum).Maximum }
}
$result.leftover_soffice = @(Get-Process soffice.bin, soffice -ErrorAction SilentlyContinue).Count
$result | ConvertTo-Json -Depth 4 | Set-Content (Join-Path $Logs 'libreoffice.json')
$result | ConvertTo-Json -Depth 4
