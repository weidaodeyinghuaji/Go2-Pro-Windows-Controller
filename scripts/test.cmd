@echo off
chcp 936 >nul
title Go2 安全运动控制器 - 离线测试
pushd "%~dp0.."

if exist ".venv\Scripts\python.exe" (
    set "TEST_PYTHON=.venv\Scripts\python.exe"
) else (
    set "TEST_PYTHON=python"
)

echo 运行离线安全策略和协议测试；不会连接 Go2。
"%TEST_PYTHON%" -m unittest discover -s tests -v
if errorlevel 1 (
    echo.
    echo [测试失败] 禁止连接真机。
    goto :end
)

echo.
echo 全部离线测试通过。

:end
popd
