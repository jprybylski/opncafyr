# Install spike #6: snapshot of what a per-user install must not change. Run as the test
# user before and after; diff the JSON.
$o = [ordered]@{}
$o.machine_path_sha = (Get-FileHash -InputStream ([IO.MemoryStream]::new([Text.Encoding]::UTF8.GetBytes([Environment]::GetEnvironmentVariable('Path','Machine'))))).Hash.Substring(0,16)
$o.user_path = [Environment]::GetEnvironmentVariable('Path','User')
$o.hklm_rcore = @(Get-ChildItem 'HKLM:\SOFTWARE\R-core\R' -ErrorAction SilentlyContinue | ForEach-Object PSChildName) -join ','
$o.hklm_rcore_current = (Get-ItemProperty 'HKLM:\SOFTWARE\R-core\R' -ErrorAction SilentlyContinue).'Current Version'
$o.hkcu_rcore = @(Get-ChildItem 'HKCU:\SOFTWARE\R-core' -Recurse -ErrorAction SilentlyContinue | ForEach-Object Name) -join ','
$o.hkcu_uninstall = @(Get-ChildItem 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall' -ErrorAction SilentlyContinue | ForEach-Object PSChildName) -join ','
$o.hkcu_rdata_assoc = [bool](Get-Item 'HKCU:\Software\Classes\.RData' -ErrorAction SilentlyContinue)
$o.program_files_r = @(Get-ChildItem 'C:\Program Files\R' -ErrorAction SilentlyContinue | ForEach-Object Name) -join ','
$o.start_menu_r = @(Get-ChildItem "$env:APPDATA\Microsoft\Windows\Start Menu\Programs" -Recurse -Filter 'R*' -ErrorAction SilentlyContinue | ForEach-Object Name) -join ','
$o.desktop = @(Get-ChildItem ([Environment]::GetFolderPath('Desktop')) -ErrorAction SilentlyContinue | ForEach-Object Name) -join ','
$o.rig_appdata = Test-Path "$env:APPDATA\rig"
$o.local_bin = Test-Path "$env:USERPROFILE\.local\bin"
$o.where_rscript = (& where.exe Rscript 2>$null) -join ','
$o | ConvertTo-Json
