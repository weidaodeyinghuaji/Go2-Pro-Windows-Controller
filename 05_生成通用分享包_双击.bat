@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\build_share.ps1"
set "PACKAGE_EXIT=%ERRORLEVEL%"
echo.
if "%PACKAGE_EXIT%"=="0" (
    echo Share package created next to this project folder.
) else (
    echo Share package creation failed. See the message above.
)
pause
endlocal & exit /b %PACKAGE_EXIT%
