[CmdletBinding()]
param(
    [string]$ProjectRoot = "",
    [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"
if (-not $ProjectRoot) {
    $ProjectRoot = Split-Path -Parent $PSScriptRoot
}
$ProjectRoot = [IO.Path]::GetFullPath($ProjectRoot)
if (-not $OutputPath) {
    $OutputPath = Join-Path (Split-Path -Parent $ProjectRoot) "机器狗安全运动控制器_通用分享版.zip"
}
$OutputPath = [IO.Path]::GetFullPath($OutputPath)
$StageRoot = Join-Path ([IO.Path]::GetTempPath()) ("dog-controller-share-" + [guid]::NewGuid().ToString("N"))
$PackageName = "机器狗安全运动控制器"
$PackageRoot = Join-Path $StageRoot $PackageName

function Should-Exclude([string]$RelativePath, [string]$Name) {
    $Segments = $RelativePath -split '[\\/]'
    if ($Segments -contains ".venv" -or $Segments -contains "__pycache__" -or $Segments -contains ".git") {
        return $true
    }
    if ($Name -match '\.(pyc|pyo|log|tmp|bak|orig|pem|key)$') {
        return $true
    }
    if ($Name -eq ".env" -or $Name -like "*.zip") {
        return $true
    }
    return $false
}

try {
    New-Item -ItemType Directory -Path $PackageRoot -Force | Out-Null
    $RootPrefix = $ProjectRoot.TrimEnd('\') + '\'
    foreach ($File in Get-ChildItem -LiteralPath $ProjectRoot -Recurse -File) {
        $Relative = $File.FullName.Substring($RootPrefix.Length)
        if (Should-Exclude $Relative $File.Name) {
            continue
        }
        $Destination = Join-Path $PackageRoot $Relative
        New-Item -ItemType Directory -Path (Split-Path -Parent $Destination) -Force | Out-Null
        Copy-Item -LiteralPath $File.FullName -Destination $Destination
    }

    $OutputParent = Split-Path -Parent $OutputPath
    if ($OutputParent) {
        New-Item -ItemType Directory -Path $OutputParent -Force | Out-Null
    }
    if (Test-Path -LiteralPath $OutputPath) {
        Remove-Item -LiteralPath $OutputPath -Force
    }
    Compress-Archive -LiteralPath $PackageRoot -DestinationPath $OutputPath -CompressionLevel Optimal
    Write-Host "Share package created: $OutputPath"
    Write-Host "Excluded: .venv, caches, logs, archives, keys, certificates and local environment files."
    exit 0
} catch {
    Write-Host "[PACKAGE FAILED] $($_.Exception.Message)" -ForegroundColor Red
    exit 1
} finally {
    if ((Test-Path -LiteralPath $StageRoot) -and $StageRoot.StartsWith([IO.Path]::GetTempPath(), [StringComparison]::OrdinalIgnoreCase)) {
        Remove-Item -LiteralPath $StageRoot -Recurse -Force
    }
}
