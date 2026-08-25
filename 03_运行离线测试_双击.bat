@echo off
chcp 936 >nul
cd /d "%~dp0"
call "%~dp0scripts\test.cmd"
echo.
pause
