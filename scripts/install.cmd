@echo off
setlocal
chcp 65001 >nul
title Dog Robot Safe Motion Controller - Setup
pushd "%~dp0.."

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
set "SETUP_EXIT=%ERRORLEVEL%"

popd
endlocal & exit /b %SETUP_EXIT%
