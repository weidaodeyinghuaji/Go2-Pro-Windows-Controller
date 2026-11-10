[CmdletBinding()]
param(
    [string]$ProjectRoot = "",
    [string]$OutputRoot = "",
    [string]$BuildTag = "standalone",
    [switch]$SkipArchive
)

$ErrorActionPreference = "Stop"
$StandaloneSuffix = -join @([char]0x514D, [char]0x5B89, [char]0x88C5, [char]0x7A33, [char]0x5B9A, [char]0x7248)
$StandaloneFolderName = "Go2 Controller_$StandaloneSuffix"
$LauncherName = (-join @([char]0x4E00, [char]0x952E, [char]0x542F, [char]0x52A8)) + ".bat"
if (-not $ProjectRoot) {
    $ProjectRoot = Split-Path -Parent $PSScriptRoot
}
$ProjectRoot = [IO.Path]::GetFullPath($ProjectRoot)
if (-not $OutputRoot) {
    $OutputRoot = Join-Path (Split-Path -Parent $ProjectRoot) $StandaloneFolderName
}
$OutputRoot = [IO.Path]::GetFullPath($OutputRoot)
$OutputParent = [IO.Path]::GetFullPath((Split-Path -Parent $OutputRoot))
if ($BuildTag -notmatch '^[a-z0-9][a-z0-9-]{0,40}$') {
    throw "BuildTag must be a short lowercase identifier."
}
$BuildRoot = Join-Path (Join-Path $ProjectRoot "build") $BuildTag
$DistRoot = Join-Path (Join-Path $ProjectRoot "dist") $BuildTag
$BundleRoot = Join-Path $DistRoot "Go2 Controller"
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Spec = Join-Path $ProjectRoot "packaging\go2_controller.spec"
$Model = Join-Path $ProjectRoot "models\yolox_tiny.onnx"
$ModelSha256 = "427CC366D34E27FF7A03E2899B5E3671425C262EA2291F88BB942BC1CC70B0F7"
$PoseModel = Join-Path $ProjectRoot "models\pose_landmarker_lite.task"
$PoseModelSha256 = "59929E1D1EE95287735DDD833B19CF4AC46D29BC7AFDDBBF6753C459690D574A"
$ArchivePath = "$OutputRoot.zip"

function Assert-SafeOutputPath {
    $Leaf = Split-Path -Leaf $OutputRoot
    if ($Leaf -ne $StandaloneFolderName) {
        throw "Refusing to replace unexpected output folder: $OutputRoot"
    }
    if ($OutputRoot -eq $OutputParent -or -not $OutputRoot.StartsWith($OutputParent + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "Output folder is outside its verified parent: $OutputRoot"
    }
}

function Invoke-Checked([string]$Executable, [string[]]$Arguments) {
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code $LASTEXITCODE`: $Executable $($Arguments -join ' ')"
    }
}

try {
    Assert-SafeOutputPath
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
        throw "Missing project Python environment. Run the project setup batch file first."
    }
    if (-not (Test-Path -LiteralPath $Model -PathType Leaf)) {
        throw "Missing YOLOX model: $Model"
    }
    $ActualModelHash = (Get-FileHash -LiteralPath $Model -Algorithm SHA256).Hash
    if (-not [string]::Equals($ActualModelHash, $ModelSha256, [StringComparison]::OrdinalIgnoreCase)) {
        throw "YOLOX model checksum mismatch. Run the project setup batch file to repair it."
    }
    if (-not (Test-Path -LiteralPath $PoseModel -PathType Leaf)) {
        throw "Missing MediaPipe pose model: $PoseModel"
    }
    $ActualPoseModelHash = (Get-FileHash -LiteralPath $PoseModel -Algorithm SHA256).Hash
    if (-not [string]::Equals($ActualPoseModelHash, $PoseModelSha256, [StringComparison]::OrdinalIgnoreCase)) {
        throw "MediaPipe pose model checksum mismatch. Run the project setup batch file to repair it."
    }
    Write-Host "[1/5] Verifying the pinned packaging toolchain..."
    Invoke-Checked $Python @(
        "-c",
        "import PyInstaller; raise SystemExit(0 if PyInstaller.__version__ == '6.22.2' else 2)"
    )

    Write-Host "[2/5] Running project tests..."
    Invoke-Checked $Python @("-m", "unittest", "discover", "-s", (Join-Path $ProjectRoot "tests"))

    Write-Host "[3/5] Building the self-contained Windows runtime..."
    Invoke-Checked $Python @(
        "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--distpath", $DistRoot,
        "--workpath", $BuildRoot,
        $Spec
    )
    if (-not (Test-Path -LiteralPath (Join-Path $BundleRoot "Go2 Controller.exe") -PathType Leaf)) {
        throw "PyInstaller completed without the expected executable."
    }

    # 真实站立者测试帧走完整条 YOLOX -> 姿态过滤链；只导入模型不能发现“没有绿框”。
    $PersonReport = Join-Path $BuildRoot "person-runtime.txt"
    $FrozenExe = Join-Path $BundleRoot "Go2 Controller.exe"
    $PersonProbe = Start-Process -FilePath $FrozenExe `
        -ArgumentList @("--check-person-runtime", ('"' + $PersonReport + '"')) `
        -WorkingDirectory $BundleRoot -WindowStyle Hidden -Wait -PassThru
    if ($PersonProbe.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $PersonReport -PathType Leaf)) {
        $Details = if (Test-Path -LiteralPath $PersonReport -PathType Leaf) {
            Get-Content -LiteralPath $PersonReport -Raw
        } else {
            "No diagnostic report was written."
        }
        throw "Frozen person-recognition runtime check failed: $Details"
    }
    $PersonResult = (Get-Content -LiteralPath $PersonReport -Raw).Trim()
    if ($PersonResult -notmatch '^OK standing=([1-9][0-9]*) size=([1-9][0-9]*)x([1-9][0-9]*)$') {
        throw "Frozen person-recognition inference check failed: $PersonResult"
    }
    Write-Host "Frozen person-recognition inference: $PersonResult"
    Remove-Item -LiteralPath $PersonReport -Force

    Write-Host "[4/5] Creating the clean release folder..."
    if (Test-Path -LiteralPath $OutputRoot) {
        Remove-Item -LiteralPath $OutputRoot -Recurse -Force
    }
    New-Item -ItemType Directory -Path $OutputRoot -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $BundleRoot "Go2 Controller.exe") -Destination $OutputRoot
    Copy-Item -LiteralPath (Join-Path $BundleRoot "_internal") -Destination $OutputRoot -Recurse

    $LauncherContents = @'
@echo off
setlocal
cd /d "%~dp0"
if not exist "%~dp0Go2 Controller.exe" (
    echo [START FAILED] Go2 Controller.exe is missing. Extract the complete package again.
    pause
    exit /b 2
)
start "" "%~dp0Go2 Controller.exe"
endlocal
'@
    $LauncherContents | Set-Content -LiteralPath (Join-Path $OutputRoot $LauncherName) -Encoding ASCII

    $ReadmeContents = @'
AI故障预警｜升压站智能巡检平台（免安装版）

1. 请复制整个文件夹，不要只复制 EXE 或 BAT；_internal 文件夹不可删除或改名。
2. 双击“一键启动.bat”。不需要另外安装 Python 或项目依赖。
3. 首次在新电脑使用时，连接机器狗、开启摄像头，再点“开启人员识别”。
4. 自动流程可独立排列“红灯 → 播放声音”；红灯不会自动播放声音。
5. 自行导入的本地声音不会因复制 EXE 而迁移。分享含声音的流程时，
   请连同流程 JSON 旁的“_声音素材”文件夹一起复制，并在新机器狗上同步一次缺少的声音。
6. 真机运动前请确认场地安全，并备好实体遥控器急停。

系统要求：64 位 Windows 10/11。AES key 不保存。
错误日志位于当前用户的 AppData\Local\Go2 Controller\logs\error.log。
'@
    $ReadmeContents | Set-Content -LiteralPath (Join-Path $OutputRoot "README.txt") -Encoding UTF8

    if ($SkipArchive) {
        Write-Host "[5/5] ZIP archive skipped; the complete folder is the deliverable."
    } else {
        Write-Host "[5/5] Creating the ZIP archive..."
        if (Test-Path -LiteralPath $ArchivePath) {
            Remove-Item -LiteralPath $ArchivePath -Force
        }
        Compress-Archive -LiteralPath $OutputRoot -DestinationPath $ArchivePath -CompressionLevel Optimal
    }

    $FileCount = (Get-ChildItem -LiteralPath $OutputRoot -Recurse -File).Count
    $SizeBytes = (Get-ChildItem -LiteralPath $OutputRoot -Recurse -File | Measure-Object -Property Length -Sum).Sum
    Write-Host ""
    Write-Host "Standalone release created successfully." -ForegroundColor Green
    Write-Host "Folder: $OutputRoot"
    if (-not $SkipArchive) { Write-Host "Archive: $ArchivePath" }
    Write-Host ("Files: {0}; Size: {1:N1} MB" -f $FileCount, ($SizeBytes / 1MB))
    exit 0
} catch {
    Write-Host ""
    Write-Host "[STANDALONE BUILD FAILED] $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
