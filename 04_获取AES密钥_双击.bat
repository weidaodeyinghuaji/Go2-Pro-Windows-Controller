@echo off
chcp 936 >nul
cd /d "%~dp0"
call "%~dp0scripts\fetch_aes_key.cmd"
echo.
pause
