# Install spike #6: clean standard-user install on Windows 11 through to rendered artifacts.
#
# Spike tooling, not the installer (#19). Run as a standard (non-admin) user:
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File windows-install.ps1 `
#       -Bundle <dir> [-Online] [-RMethod inno|rig] [-Steps verify,uv,...]
#
# <dir> holds assets.json (scripts/spikes/spike_assets.py --platform windows-x86_64),
# assets\ (pinned files; with -Online, missing ones are downloaded from their pinned URLs),
# src\ (renv.lock, tools\{pyproject.toml,uv.lock,requirements.txt}, examples\demo-report)
# and scripts\ (restore_r.R, smoke_opnca.R). Every file is checked against its pinned
# SHA-256 before use. Everything goes under -Root; nothing touches machine PATH, HKLM or
# the user's default R. Each step is logged to <Root>\logs\steps.jsonl.
param(
    [string]$Root = (Join-Path $env:LOCALAPPDATA 'opncafyr\spike'),
    [Parameter(Mandatory = $true)][string]$Bundle,
    [switch]$Online,
    [ValidateSet('inno', 'rig')][string]$RMethod = 'inno',
    [string[]]$Steps = @('verify', 'uv', 'python', 'tools', 'r', 'rlib', 'opnca', 'serve', 'quarto', 'deck', 'report')
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
# With -File, "-Steps a,b" arrives as one string.
$Steps = @($Steps | ForEach-Object { $_ -split ',' } | Where-Object { $_ })

$Assets = Join-Path $Bundle 'assets'
$Src = Join-Path $Bundle 'src'
$Scripts = Join-Path $Bundle 'scripts'
$Logs = Join-Path $Root 'logs'
New-Item -ItemType Directory -Force $Root, $Logs | Out-Null
$StepLog = Join-Path $Logs 'steps.jsonl'
$Manifest = Get-Content (Join-Path $Bundle 'assets.json') -Raw | ConvertFrom-Json

function Asset([string]$kind, [string]$name) {
    $a = @($Manifest | Where-Object { $_.kind -eq $kind -and ($name -eq '' -or $_.name -eq $name) })
    if ($a.Count -ne 1) { throw "expected one asset $kind/$name, found $($a.Count)" }
    Join-Path $Assets $a[0].local_name
}

function Invoke-Native([string]$exe, [string[]]$argv, [string]$log) {
    # argv array, no shell; output to a log file; non-zero exit throws.
    $out = if ($log) { Join-Path $Logs $log } else { Join-Path $Logs 'native.log' }
    # Windows PowerShell 5.1 turns redirected native stderr into errors; 'Stop' would
    # abort on the first progress line, so judge success by the exit code only.
    $eap = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    try { & $exe @argv *>> $out } finally { $ErrorActionPreference = $eap }
    if ($LASTEXITCODE -ne 0) { throw "$([IO.Path]::GetFileName($exe)) exited $LASTEXITCODE (see $out)" }
}

function Step([string]$name, [scriptblock]$body) {
    if ($Steps -notcontains $name) { return }
    $t0 = Get-Date
    $status = 'ok'; $detail = ''
    try { $detail = (& $body | Out-String).Trim() }
    catch { $status = 'failed'; $detail = $_.Exception.Message }
    $secs = [math]::Round(((Get-Date) - $t0).TotalSeconds, 1)
    $rec = [ordered]@{ step = $name; status = $status; started = $t0.ToUniversalTime().ToString('s') + 'Z'; seconds = $secs; online = [bool]$Online; r_method = $RMethod; detail = $detail }
    ($rec | ConvertTo-Json -Compress) | Add-Content $StepLog
    Write-Host ("[{0,-6}] {1,-7} {2,7}s  {3}" -f $status, $name, $secs, $detail)
    if ($status -ne 'ok') { exit 1 }
}

# App-controlled environment for every child: app tools first on PATH, no user R profile,
# app-owned caches, never a uv or Python download behind our back.
$env:UV_CACHE_DIR = Join-Path $Root 'cache\uv'
$env:UV_PYTHON_INSTALL_DIR = Join-Path $Root 'python'
$env:UV_PYTHON_DOWNLOADS = 'never'
$env:UV_NO_CONFIG = '1'
$env:R_ENVIRON_USER = 'NUL'
$env:R_PROFILE_USER = 'NUL'
$env:R_LIBS_SITE = ''
$env:RENV_PATHS_ROOT = Join-Path $Root 'cache\renv'
$env:RENV_CONFIG_AUTOLOADER_ENABLED = 'FALSE'
$Lib = Join-Path $Root 'rlib'
$env:R_LIBS_USER = $Lib
$PyHome = Join-Path $Root 'python\cpython-3.13.18'
$Python = Join-Path $PyHome 'python.exe'
$Venv = Join-Path $Root 'tools-venv'
$VenvPy = Join-Path $Venv 'Scripts\python.exe'
$UvDir = Join-Path $Root 'uv'
$Uv = Join-Path $UvDir 'uv.exe'
$QuartoDir = Join-Path $Root 'quarto'
$Quarto = Join-Path $QuartoDir 'bin\quarto.exe'
$env:PATH = "$UvDir;$env:PATH"
$Work = Join-Path $Root 'work'
New-Item -ItemType Directory -Force $Work | Out-Null

function Stop-Tree($proc) {
    if ($proc.HasExited) { return }
    $eap = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    try { & taskkill.exe /T /F /PID $proc.Id *> $null } finally { $ErrorActionPreference = $eap }
}

function Find-Rscript {
    $hit = Get-ChildItem (Join-Path $Root 'r') -Recurse -Filter Rscript.exe -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -match '\\bin\\x64\\Rscript\.exe$' } | Select-Object -First 1
    if (-not $hit) { throw 'Rscript.exe not found under the app root' }
    $hit.FullName
}

Step 'verify' {
    New-Item -ItemType Directory -Force $Assets | Out-Null
    $n = 0; $fetched = 0
    foreach ($a in $Manifest) {
        if ($a.kind -eq 'missing-wheel') { continue }
        $path = Join-Path $Assets $a.local_name
        if (-not (Test-Path $path)) {
            if (-not $Online) { throw "missing $($a.local_name) and -Online not given" }
            & curl.exe -sfL --retry 3 -o $path $a.url
            if ($LASTEXITCODE -ne 0) { throw "download failed: $($a.url)" }
            $fetched++
        }
        $h = (Get-FileHash -Algorithm SHA256 $path).Hash.ToLower()
        if ($h -ne $a.sha256) { Remove-Item $path; throw "SHA-256 mismatch: $($a.local_name)" }
        $n++
    }
    "$n files verified, $fetched downloaded"
}

Step 'uv' {
    Expand-Archive -Force (Asset 'uv' 'uv') $UvDir
    if (-not (Test-Path $Uv)) { $Uv = (Get-ChildItem $UvDir -Recurse -Filter uv.exe | Select-Object -First 1).FullName }
    & $Uv --version
}

Step 'python' {
    # Extract the verified python-build-standalone archive ourselves. `uv python install`
    # (even from a local UV_PYTHON_INSTALL_MIRROR) also creates a cpython-3.13-* junction,
    # and on Windows 11 a junction made by a standard user is an "untrusted mount point"
    # (os error 448) that the same user can't traverse. An exact path avoids junctions.
    if (Test-Path $PyHome) { Remove-Item -Recurse -Force $PyHome }
    New-Item -ItemType Directory -Force $PyHome | Out-Null
    Invoke-Native 'tar.exe' @('-xzf', (Asset 'python' 'python'), '-C', $PyHome, '--strip-components=1') 'python.log'
    & $Python -c "import sys, ssl; print(sys.version.split()[0], ssl.OPENSSL_VERSION)"
}

Step 'tools' {
    if (Test-Path $Venv) { Remove-Item -Recurse -Force $Venv }
    Invoke-Native $Uv @('venv', '--quiet', '--python', $Python, $Venv) 'tools.log'
    if ($Online) {
        # Online variant: uv sync straight from the lock (PyPI + the deckifyr release URL).
        $env:UV_PROJECT_ENVIRONMENT = $Venv
        Push-Location (Join-Path $Src 'tools')
        try { Invoke-Native $Uv @('sync', '--locked', '--no-install-project', '--python', $Python) 'tools.log' }
        finally { Pop-Location; Remove-Item Env:UV_PROJECT_ENVIRONMENT }
    } else {
        # Offline: the same lock exported with hashes, installed from the verified wheels.
        Invoke-Native $Uv @('pip', 'install', '--quiet', '--python', $VenvPy, '--offline', '--no-index',
            '--find-links', $Assets, '--require-hashes', '-r', (Join-Path $Src 'tools\requirements.txt')) 'tools.log'
    }
    & $VenvPy -c "import deckifyr, docx, yaml, PIL, fastapi, uvicorn, pymupdf; print('deckifyr', deckifyr.__version__)"
}

Step 'r' {
    $installer = Asset 'r' 'r'
    $dest = Join-Path $Root 'r'
    if ($RMethod -eq 'inno') {
        $log = Join-Path $Logs 'r-inno.log'
        $p = Start-Process -FilePath $installer -Wait -PassThru -ArgumentList @(
            '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/CURRENTUSER', '/NOICONS',
            "/DIR=`"$dest\4.6.2`"", '/MERGETASKS="!desktopicon,!quicklaunchicon,!recordversion,!associate"',
            "/LOG=`"$log`"")
        if ($p.ExitCode -ne 0) { throw "R installer exited $($p.ExitCode)" }
    } else {
        $rigDir = Join-Path $Root 'rig'
        Expand-Archive -Force (Join-Path $Assets 'rig-windows-x86_64-0.11.1.zip') $rigDir
        $rig = (Get-ChildItem $rigDir -Recurse -Filter rig.exe | Select-Object -First 1).FullName
        $env:RIG_R_INSTALL_DIR = $dest
        $env:RIG_BINARY_DIR = Join-Path $rigDir 'bin'
        $env:RIG_CACHE_DIR = Join-Path $Root 'cache\rig'
        $env:RIG_CONFIG_FILE = Join-Path $rigDir 'config.json'
        # Per-command user mode; --no-default/--without-path keep rig away from the user's
        # default R and PATH, --without-pak/--without-repos stop it fetching pak or
        # writing repository settings. Offline, rig gets the verified installer as a URL.
        $common = @('--no-default', '--without-path', '--without-pak', '--without-repos', '--without-translations')
        $what = if ($Online) { '4.6.2' } else { 'file:///' + ($installer -replace '\\', '/') }
        Invoke-Native $rig (@('--user', 'add', $what) + $common) 'r-rig.log'
    }
    $rs = Find-Rscript
    # Single quotes inside: Windows PowerShell 5.1 drops embedded double quotes in native args.
    & $rs --vanilla -e 'cat(R.version.string, R.home(), sep = ''; '')'
}

Step 'rlib' {
    $rs = Find-Rscript
    $repo = Join-Path $Root 'rrepo'
    $bin = Join-Path $repo 'bin\windows\contrib\4.6'
    $srcDir = Join-Path $repo 'src\contrib'
    $cellar = Join-Path $Root 'cellar'
    New-Item -ItemType Directory -Force $bin, $srcDir, $cellar | Out-Null
    foreach ($a in $Manifest | Where-Object kind -eq 'r-binary') { Copy-Item (Join-Path $Assets $a.local_name) $bin -Force }
    # The pinned tool packages go into the renv cellar *and* the repository indexes (renv
    # plans the install from the source index; see local_repo.R).
    $opnca = $Manifest | Where-Object { $_.kind -eq 'package:opnca-r' -and $_.local_name -like '*windows-x86_64.zip' }
    Copy-Item (Join-Path $Assets $opnca.local_name) (Join-Path $cellar 'opnca_0.3.3.zip') -Force
    Copy-Item (Join-Path $Assets $opnca.local_name) (Join-Path $bin 'opnca_0.3.3.zip') -Force
    foreach ($a in $Manifest | Where-Object { $_.kind -in 'package:quartifyr', 'package:reportifyr', 'package:pyro' }) {
        Copy-Item (Join-Path $Assets $a.local_name) $cellar -Force
        Copy-Item (Join-Path $Assets $a.local_name) $srcDir -Force
    }
    Invoke-Native $rs @('--vanilla', (Join-Path $Scripts 'local_repo.R'), $bin, $srcDir, 'win.binary') 'rlib.log'
    Invoke-Native $rs @('--vanilla', (Join-Path $Scripts 'restore_r.R'), (Join-Path $Src 'renv.lock'), $repo, $cellar, $Lib) 'rlib.log'
    Get-Content (Join-Path $Logs 'rlib.log') | Select-String '^(restore|locked):'
}

Step 'opnca' {
    $rs = Find-Rscript
    Invoke-Native $rs @('--vanilla', (Join-Path $Scripts 'smoke_opnca.R'), $Lib) 'opnca.log'
    Get-Content (Join-Path $Logs 'opnca.log') | Select-String '^(opnca|P1)'
}

Step 'serve' {
    $rs = Find-Rscript
    $data = Join-Path $Work 'pk.csv'
    @('USUBJID,ACTUAL_TIME,CONC,DOSE,ROUTE', 'P1,0,0,100,extravascular', 'P1,1,10,100,extravascular',
      'P1,2,5,100,extravascular', 'P1,4,2.5,100,extravascular', 'P1,8,0.625,100,extravascular') | Set-Content $data -Encoding ascii
    $out = Join-Path $Work 'opnca-out'
    if (Test-Path $out) { Remove-Item -Recurse -Force $out }
    New-Item -ItemType Directory -Force $out | Out-Null
    $l = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0); $l.Start(); $port = $l.LocalEndpoint.Port; $l.Stop()
    $env:OPNCA_API_TOKEN = [guid]::NewGuid().ToString('N')
    $env:OPNCA_SHUTDOWN_TOKEN = [guid]::NewGuid().ToString('N')
    $env:R_LIBS = $Lib
    $serve = Join-Path $Lib 'opnca\scripts\serve.R'
    $p = Start-Process -FilePath $rs -PassThru -WindowStyle Hidden -RedirectStandardError (Join-Path $Logs 'serve.err.log') `
        -RedirectStandardOutput (Join-Path $Logs 'serve.out.log') -ArgumentList @('--vanilla', "`"$serve`"",
        "--data-path=`"$data`"", "--output-dir=`"$out`"", "--port=$port", '--base-path=/apps/opnca/',
        '--lock-dataset', '--no-persist-mapping', '--storage-namespace=spike', '--no-overwrite',
        '--require-blq-rules', '--api-token-env=OPNCA_API_TOKEN', '--shutdown-token-env=OPNCA_SHUTDOWN_TOKEN')
    try {
        $deadline = (Get-Date).AddSeconds(90); $body = $null
        while ((Get-Date) -lt $deadline -and -not $body) {
            Start-Sleep -Milliseconds 500
            if ($p.HasExited) { throw "serve.R exited $($p.ExitCode)" }
            try { $body = (Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:$port/apps/opnca/api/v1/health" -TimeoutSec 2).Content } catch { }
        }
        if (-not $body) { throw 'no health answer within 90 s' }
        $noToken = try { (Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:$port/apps/opnca/api/v1/meta" -TimeoutSec 5).StatusCode } catch { [int]$_.Exception.Response.StatusCode }
        "health: $body; api without token: $noToken"
    } finally {
        Stop-Tree $p
    }
}

Step 'quarto' {
    Expand-Archive -Force (Asset 'quarto' 'quarto') $QuartoDir
    & $Quarto --version
}

Step 'deck' {
    $rs = Find-Rscript
    $deck = Join-Path $Work 'deck'
    New-Item -ItemType Directory -Force $deck | Out-Null
    # UTF-8 without a BOM: PowerShell 5.1's -Encoding utf8 writes one, and deckifyr 0.3.5
    # then reads the first key as "\ufeffdeckifyr" (E_SCHEMA_VALIDATION).
    $utf8 = New-Object Text.UTF8Encoding($false)
    [IO.File]::WriteAllText((Join-Path $deck 'design.yaml'), "deckifyr: '0.1'`nslide: {width: 10in, height: 5.625in}`nfonts: {body: Arial, heading: Arial}`n", $utf8)
    [IO.File]::WriteAllText((Join-Path $deck 'layouts.yaml'), "deckifyr: '0.1'`nlayouts: {blank: {}}`n", $utf8)
    [IO.File]::WriteAllText((Join-Path $deck 'presentation.yaml'), ("deckifyr: '0.1'`ndesign: {base: design.yaml}`nlayouts: layouts.yaml`n" +
        "metadata: {title: Install spike}`nbuild: {output: build/deck.pptx, previews: false}`nslides:`n" +
        "  - id: only-slide`n    layout: blank`n    elements: {}`n"), $utf8)
    Invoke-Native $VenvPy @('-m', 'deckifyr', '--json', 'build', (Join-Path $deck 'presentation.yaml'),
        '--trusted-binaries-only', "--rscript-bin=$rs", "--quarto-bin=$Quarto") 'deck.log'
    $pptx = Join-Path $deck 'build\deck.pptx'
    if (-not (Test-Path $pptx)) { throw 'no deck.pptx' }
    $sig = [Text.Encoding]::ASCII.GetString([IO.File]::ReadAllBytes($pptx)[0..1])
    "deck.pptx $((Get-Item $pptx).Length) bytes, starts with $sig"
}

Step 'report' {
    $rs = Find-Rscript
    $proj = Join-Path $Work 'report'
    if (Test-Path $proj) { Remove-Item -Recurse -Force $proj }
    Copy-Item -Recurse (Join-Path $Src 'examples\demo-report') $proj
    # reportifyr's fill pass runs through pyro, which wants <project>\.venv and the
    # .report_init.json marker. reportifyr::initialize_report_project() would create both
    # by running pyro::initialize_python() (curl-installs uv, `uv lock`, `uv sync` from
    # PyPI), so build them without it: the venv from the verified wheels, the marker with
    # reportifyr's own writer.
    Invoke-Native $Uv @('venv', '--quiet', '--python', $Python, (Join-Path $proj '.venv')) 'report.log'
    Invoke-Native $Uv @('pip', 'install', '--quiet', '--python', (Join-Path $proj '.venv\Scripts\python.exe'), '--offline',
        '--no-index', '--find-links', $Assets, '--require-hashes', '-r', (Join-Path $Src 'tools\requirements.txt')) 'report.log'
    $p = $proj -replace '\\', '/'
    Invoke-Native $rs @('--vanilla', '-e', "reportifyr:::create_init_file('$p', '$p/report', '$p/OUTPUTS')") 'report.log'
    $env:QUARTIFYR_PYTHON_ENV = $Venv
    $env:QUARTO_R = $rs
    $env:QUARTO_PYTHON = $VenvPy
    $render = Join-Path $Lib 'quartifyr\scripts\render.R'
    $json = Join-Path $Logs 'report.json'
    Push-Location $proj
    $eap = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    try {
        & $rs --no-init-file $render --project $proj --shell report.qmd --json --strict --offline --quarto-bin $Quarto `
            1> $json 2>> (Join-Path $Logs 'report.log')
        $code = $LASTEXITCODE
    } finally { Pop-Location; $ErrorActionPreference = $eap }
    $result = Get-Content $json -Raw | ConvertFrom-Json
    if ($code -ne 0) { throw "render.R exited $code, status $($result.status)" }
    $docx = Get-ChildItem $proj -Recurse -Filter *.docx | Where-Object FullName -notmatch '\\shell\\' | Sort-Object LastWriteTime | Select-Object -Last 1
    "status $($result.status); $($docx.Name) $($docx.Length) bytes"
}

# Repair/exception path only (ADR 0004), not in the default steps: build the vendored
# opnca-r source tarball with a per-user Rtools45 and the standalone Rust GNU toolchain.
# Needs <Bundle>\repair\{rtools45-*.exe, rust-*-x86_64-pc-windows-gnu.tar.xz,
# opnca_*-vendored.tar.gz}, each checked against its pin (pass -RepairPins as JSON).
Step 'repair' {
    $rs = Find-Rscript
    $repair = Join-Path $Bundle 'repair'
    $rtoolsExe = Get-ChildItem $repair -Filter 'rtools45-*.exe' | Select-Object -First 1 -ExpandProperty FullName
    $rustTar = Get-ChildItem $repair -Filter 'rust-*-x86_64-pc-windows-gnu.tar.xz' | Select-Object -First 1 -ExpandProperty FullName
    $vendored = Get-ChildItem $repair -Filter 'opnca_*-vendored.tar.gz' | Select-Object -First 1 -ExpandProperty FullName
    $pins = @{
        'rtools45-6768-6492.exe' = '614c7378150a012e70b16edcfe5236dcead47f491f1f54203ea8d451c7743a75'
        'rust-1.99.1-x86_64-pc-windows-gnu.tar.xz' = '0210624b2eb2bdf4ec32c2a6caf5c3bd2ab84e5df66c7530023e37f02add1084'
        'opnca_0.3.3-vendored.tar.gz' = 'e918cf8a6de80a63ef795250abf061fac75b5b440a2e816b9bcfab0d4da848cb'
    }
    foreach ($f in $rtoolsExe, $rustTar, $vendored) {
        if ((Get-FileHash -Algorithm SHA256 $f).Hash.ToLower() -ne $pins[[IO.Path]::GetFileName($f)]) { throw "SHA-256 mismatch: $f" }
    }
    $times = [ordered]@{}
    $rtools = Join-Path $Root 'rtools45'
    $t0 = Get-Date
    $p = Start-Process -FilePath $rtoolsExe -Wait -PassThru -ArgumentList @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART',
        '/CURRENTUSER', '/NOICONS', "/DIR=`"$rtools`"", "/LOG=`"$Logs\rtools.log`"")
    if ($p.ExitCode -ne 0) { throw "Rtools installer exited $($p.ExitCode)" }
    $times.rtools = [math]::Round(((Get-Date) - $t0).TotalSeconds)
    $t0 = Get-Date
    $rustSrc = Join-Path $Root 'cache\rust-standalone'
    $rust = Join-Path $Root 'rust'
    New-Item -ItemType Directory -Force $rustSrc, $rust | Out-Null
    Invoke-Native 'tar.exe' @('-xf', $rustTar, '-C', $rustSrc, '--strip-components=1') 'repair.log'
    # The standalone archive holds one folder per component plus install.sh; on Windows
    # merge the components' trees into one prefix instead of running install.sh.
    foreach ($c in Get-Content (Join-Path $rustSrc 'components')) {
        Get-ChildItem (Join-Path $rustSrc $c) -Exclude 'manifest.in' | Copy-Item -Destination $rust -Recurse -Force
    }
    $times.rust = [math]::Round(((Get-Date) - $t0).TotalSeconds)
    $env:RTOOLS45_HOME = $rtools
    $env:PATH = "$rust\bin;$rtools\x86_64-w64-mingw32.static.posix\bin;$rtools\usr\bin;$env:PATH"
    $env:NOT_CRAN = ''
    $srcLib = Join-Path $Root 'rlib-source'
    New-Item -ItemType Directory -Force $srcLib | Out-Null
    $t0 = Get-Date
    $r = Join-Path (Split-Path $rs) 'R.exe'
    Invoke-Native $r @('CMD', 'INSTALL', "--library=$srcLib", $vendored) 'repair.log'
    $times.build = [math]::Round(((Get-Date) - $t0).TotalSeconds)
    Invoke-Native $rs @('--vanilla', (Join-Path $Scripts 'smoke_opnca.R'), $srcLib, $Lib) 'repair.log'
    & (Join-Path $rust 'bin\rustc.exe') -V
    "rtools $($times.rtools)s, rust $($times.rust)s, build $($times.build)s; $(Get-Content (Join-Path $Logs 'repair.log') | Select-String '^opnca ' | Select-Object -Last 1)"
}
