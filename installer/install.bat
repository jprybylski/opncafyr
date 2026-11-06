@echo off
rem opncafyr per-user installer for Windows (#19). No administrator rights required.
rem Runs install.ps1 for this process only; it does not change machine policy.
rem   install.bat [install|repair|uninstall|status] [options]   (see install.ps1)
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
exit /b %ERRORLEVEL%
