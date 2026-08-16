@echo off
rem ============================================================
rem  停止"截图转题目 Word"后台服务
rem ============================================================
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop_server.ps1"
pause
