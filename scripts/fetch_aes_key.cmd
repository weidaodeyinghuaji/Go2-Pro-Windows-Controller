@echo off
chcp 936 >nul
title Go2 安全运动控制器 - 获取 AES 密钥
pushd "%~dp0.."

if not exist ".venv\Scripts\python.exe" (
    echo [错误] 没找到 .venv。
    echo 请先双击：01_安装环境_双击.bat
    goto :end
)

echo 密码采用隐藏输入；账号密码和 AES key 均不会写入项目文件。
echo.
set /p "UNITREE_ACCOUNT=请输入绑定 Go2 的 Unitree 账号邮箱："
if "%UNITREE_ACCOUNT%"=="" (
    echo [错误] 账号邮箱不能为空。
    goto :end
)

set /p "REGION_CHOICE=请选择 [1=中国大陆 cn，2=国际区 global，默认 1]："
if "%REGION_CHOICE%"=="" set "REGION_CHOICE=1"
if "%REGION_CHOICE%"=="1" set "CLOUD_REGION=cn"
if "%REGION_CHOICE%"=="2" set "CLOUD_REGION=global"
if not defined CLOUD_REGION (
    echo [错误] 只能输入 1 或 2。
    goto :end
)

echo 接下来输入密码；输入内容不会显示。
".venv\Scripts\python.exe" -X utf8 "fetch_aes_key.py" --email "%UNITREE_ACCOUNT%" --region "%CLOUD_REGION%" --device-type Go2
if errorlevel 1 echo [获取失败] 请检查账号、区域和机器人绑定状态。

:end
popd
