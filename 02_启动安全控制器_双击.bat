@echo off
chcp 936 >nul
cd /d "%~dp0"
call "%~dp0scripts\start.cmd"
echo.
pause
