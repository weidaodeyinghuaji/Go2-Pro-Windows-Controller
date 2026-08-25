@echo off
chcp 936 >nul
title Go2 Pro Windows 安全运动控制器
pushd "%~dp0.."

if not exist ".venv\Scripts\python.exe" (
    echo [错误] 没找到 .venv。
    echo 请先双击：01_安装环境_双击.bat
    goto :end
)

echo 即将打开图形控制器。
echo 真机运行前必须确认硬件故障已排除，并准备实体遥控器急停。
echo.
".venv\Scripts\python.exe" "main.py"
if errorlevel 1 (
    echo.
    echo [程序异常结束] 请查看上方错误。
)

:end
popd
