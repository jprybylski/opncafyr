@echo off
rem opncafyr per-user installer for Windows. No administrator rights required.
rem Runs install.ps1 for this process only; it does not change machine policy.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
exit /b %ERRORLEVEL%
