@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
"%~dp0.venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r "%~dp0requirements-build.txt"
if errorlevel 1 goto :failed
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\build_standalone.ps1"
if errorlevel 1 goto :failed
echo.
echo [完成] 免安装文件夹和 ZIP 已生成在当前项目的上一级目录。
pause
exit /b 0

:failed
echo.
echo [失败] 请查看上方第一条错误。
pause
exit /b 1
