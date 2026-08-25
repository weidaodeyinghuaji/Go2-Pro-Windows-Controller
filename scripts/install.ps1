[CmdletBinding()]
param(
    [string]$ProjectRoot = "",
    [string]$PythonExecutable = "",
    [switch]$SkipDependencies
)

$ErrorActionPreference = "Stop"
if (-not $ProjectRoot) {
    $ProjectRoot = Split-Path -Parent $PSScriptRoot
}
$ProjectRoot = [IO.Path]::GetFullPath($ProjectRoot)
$VenvPath = Join-Path $ProjectRoot ".venv"
$VenvPython = Join-Path $VenvPath "Scripts\python.exe"
$VenvMarker = Join-Path $VenvPath ".go2-setup.json"
$PersonModelDirectory = Join-Path $ProjectRoot "models"
$PersonModelPath = Join-Path $PersonModelDirectory "yolox_tiny.onnx"
$PersonModelUrl = "https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_tiny.onnx"
$PersonModelSha256 = "427CC366D34E27FF7A03E2899B5E3671425C262EA2291F88BB942BC1CC70B0F7"

function Test-CompatiblePython([string]$Executable) {
    if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) {
        return $false
    }
    & $Executable -c "import struct, sys, tkinter; raise SystemExit(0 if (3, 10) <= sys.version_info[:2] < (3, 15) and struct.calcsize('P') * 8 == 64 else 2)" 2>$null
    return $LASTEXITCODE -eq 0
}

function Resolve-CompatiblePython {
    $Candidates = New-Object System.Collections.Generic.List[string]
    if ($PythonExecutable) {
        $Candidates.Add([IO.Path]::GetFullPath($PythonExecutable))
    } else {
        $PreviousErrorAction = $ErrorActionPreference
        $ErrorActionPreference = "SilentlyContinue"
        try {
            $PythonCommand = Get-Command "python.exe" -ErrorAction SilentlyContinue
            if ($PythonCommand) {
                $Candidates.Add($PythonCommand.Source)
            }
            $Launcher = Get-Command "py.exe" -ErrorAction SilentlyContinue
            if ($Launcher) {
                foreach ($Version in @("3.14", "3.13", "3.12", "3.11", "3.10")) {
                    $Resolved = & $Launcher.Source "-$Version" -c "import sys; print(sys.executable)" 2>$null
                    if ($LASTEXITCODE -eq 0 -and $Resolved) {
                        $Candidates.Add([string]($Resolved | Select-Object -First 1))
                    }
                }
            }
        } finally {
            $ErrorActionPreference = $PreviousErrorAction
        }
    }

    foreach ($Candidate in ($Candidates | Select-Object -Unique)) {
        if (Test-CompatiblePython $Candidate) {
            return [IO.Path]::GetFullPath($Candidate)
        }
    }
    return $null
}

function Invoke-Checked([string]$Executable, [string[]]$Arguments) {
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code $LASTEXITCODE`: $Executable $($Arguments -join ' ')"
    }
}

function Test-PersonModel {
    if (-not (Test-Path -LiteralPath $PersonModelPath -PathType Leaf)) {
        return $false
    }
    $ActualHash = (Get-FileHash -LiteralPath $PersonModelPath -Algorithm SHA256).Hash
    return [string]::Equals($ActualHash, $PersonModelSha256, [StringComparison]::OrdinalIgnoreCase)
}

function Install-PersonModel {
    if (Test-PersonModel) {
        Write-Host "Person detection model is already valid."
        return
    }
    New-Item -ItemType Directory -Path $PersonModelDirectory -Force | Out-Null
    $DownloadPath = Join-Path $PersonModelDirectory ".yolox_tiny.onnx.download"
    try {
        # Windows PowerShell 5.1 在部分电脑上仍需显式启用 TLS 1.2。
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -Uri $PersonModelUrl -OutFile $DownloadPath -UseBasicParsing
        $DownloadedHash = (Get-FileHash -LiteralPath $DownloadPath -Algorithm SHA256).Hash
        if (-not [string]::Equals($DownloadedHash, $PersonModelSha256, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Downloaded YOLOX model checksum mismatch."
        }
        Move-Item -LiteralPath $DownloadPath -Destination $PersonModelPath -Force
    } finally {
        if (Test-Path -LiteralPath $DownloadPath -PathType Leaf) {
            Remove-Item -LiteralPath $DownloadPath -Force
        }
    }
}

function Test-ExistingVenv {
    if (-not (Test-Path -LiteralPath $VenvPython -PathType Leaf)) {
        return $false
    }
    $BelongsToThisProject = $false
    try {
        if (Test-Path -LiteralPath $VenvMarker -PathType Leaf) {
            $Marker = Get-Content -LiteralPath $VenvMarker -Raw -Encoding UTF8 | ConvertFrom-Json
            $BelongsToThisProject = [string]::Equals(
                [IO.Path]::GetFullPath([string]$Marker.project_root),
                $ProjectRoot,
                [StringComparison]::OrdinalIgnoreCase
            )
        } else {
            $ConfigPath = Join-Path $VenvPath "pyvenv.cfg"
            foreach ($Line in Get-Content -LiteralPath $ConfigPath -Encoding UTF8) {
                $Separator = " -m venv "
                $Index = $Line.IndexOf($Separator, [StringComparison]::OrdinalIgnoreCase)
                if ($Line.StartsWith("command = ", [StringComparison]::OrdinalIgnoreCase) -and $Index -ge 0) {
                    $CreatedAt = $Line.Substring($Index + $Separator.Length).Trim().Trim('"')
                    $BelongsToThisProject = [string]::Equals(
                        [IO.Path]::GetFullPath($CreatedAt),
                        $VenvPath,
                        [StringComparison]::OrdinalIgnoreCase
                    )
                    break
                }
            }
        }
    } catch {
        return $false
    }
    if (-not $BelongsToThisProject) {
        return $false
    }
    & $VenvPython -c "import sys, tkinter; raise SystemExit(0 if sys.prefix != sys.base_prefix else 3)" 2>$null
    return $LASTEXITCODE -eq 0
}

try {
    Write-Host "Dog Robot Safe Motion Controller - portable setup"
    Write-Host "Project: $ProjectRoot"

    $Python = Resolve-CompatiblePython
    if (-not $Python) {
        Write-Host "[ERROR] No compatible 64-bit Python with Tk was found." -ForegroundColor Red
        Write-Host "Install Python 3.11 x64, enable 'Add Python to PATH', then run setup again."
        Write-Host "Official download: https://www.python.org/downloads/release/python-3119/"
        exit 2
    }

    $Version = & $Python -c "import platform; print(platform.python_version())"
    Write-Host "Using: $Python (Python $Version)"

    if (Test-ExistingVenv) {
        Write-Host "[1/5] Existing virtual environment is valid."
    } else {
        if (Test-Path -LiteralPath $VenvPath) {
            if ([IO.Path]::GetFileName($VenvPath) -ne ".venv") {
                throw "Refusing to remove unexpected path: $VenvPath"
            }
            Write-Host "[1/5] Copied or broken .venv detected; rebuilding it for this computer."
            Remove-Item -LiteralPath $VenvPath -Recurse -Force
        } else {
            Write-Host "[1/5] Creating a virtual environment for this computer."
        }
        Invoke-Checked $Python @("-m", "venv", $VenvPath)
        @{
            project_root = $ProjectRoot
            base_python = $Python
        } | ConvertTo-Json | Set-Content -LiteralPath $VenvMarker -Encoding UTF8
        if (-not (Test-ExistingVenv)) {
            throw "The new virtual environment did not pass validation."
        }
    }

    if ($SkipDependencies) {
        Write-Host "[TEST] Virtual environment portability check passed."
        exit 0
    }

    Write-Host "[2/5] Updating pip and build tools..."
    Invoke-Checked $VenvPython @("-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel")

    Write-Host "[3/5] Installing controller runtime dependencies..."
    Invoke-Checked $VenvPython @("-m", "pip", "install", "-r", (Join-Path $ProjectRoot "requirements-runtime.txt"))
    Invoke-Checked $VenvPython @("-m", "pip", "install", "--no-deps", "-r", (Join-Path $ProjectRoot "requirements.txt"))

    Write-Host "[4/5] Downloading/verifying YOLOX-Tiny person detection model..."
    Install-PersonModel

    Write-Host "[5/5] Verifying imports..."
    Invoke-Checked $VenvPython @(
        "-c",
        "import tkinter, aiortc, curl_cffi, onnxruntime, sounddevice, unitree_webrtc_connect; from PIL import Image; from Crypto.Cipher import AES; print('Environment verification passed')"
    )

    Write-Host ""
    Write-Host "Setup complete. Run the offline-test batch file before connecting a robot." -ForegroundColor Green
    exit 0
} catch {
    Write-Host ""
    Write-Host "[SETUP FAILED] $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Do not copy the .venv folder to another computer; use the share-package script."
    exit 1
}
