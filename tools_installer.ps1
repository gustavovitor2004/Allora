<#
.SYNOPSIS
    Downloads a portable ffmpeg or Poppler build into tools/<component>,
    called by Allora.bat on first run. Never touches the system PATH or
    requires administrator rights - the app itself knows to look inside
    tools/ as a fallback (see utils.find_ffmpeg / utils.find_poppler_bin_dir).

.PARAMETER Component
    "ffmpeg" or "poppler".

.PARAMETER ToolsDir
    Path to the project's tools/ folder (created if missing).

.NOTES
    Exit code 0 on success, 1 on any failure - Allora.bat checks this to
    decide whether it's safe to write the .allora_installed marker.
#>
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("ffmpeg", "poppler")]
    [string]$Component,

    [Parameter(Mandatory = $true)]
    [string]$ToolsDir
)

$ErrorActionPreference = "Stop"

function Install-Ffmpeg {
    param([string]$ToolsDir)

    $tmpDir = Join-Path $ToolsDir "ffmpeg_tmp"
    if (Test-Path $tmpDir) { Remove-Item $tmpDir -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $tmpDir | Out-Null

    # BtbN maintains this exact URL as a permanent pointer to the newest
    # build - not a version-pinned link that goes stale.
    $url = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
    $zipPath = Join-Path $tmpDir "ffmpeg.zip"

    Write-Host "Baixando ffmpeg..."
    Invoke-WebRequest -Uri $url -OutFile $zipPath -UseBasicParsing

    Write-Host "Extraindo ffmpeg..."
    Expand-Archive -Path $zipPath -DestinationPath $tmpDir -Force

    $exe = Get-ChildItem -Path $tmpDir -Recurse -Filter "ffmpeg.exe" | Select-Object -First 1
    if (-not $exe) {
        throw "ffmpeg.exe não foi encontrado dentro do pacote baixado."
    }

    $destDir = Join-Path $ToolsDir "ffmpeg"
    New-Item -ItemType Directory -Force -Path $destDir | Out-Null
    # Copy just the contents of the folder containing ffmpeg.exe (which
    # also has ffprobe.exe and any needed DLLs) into a stable, predictable
    # tools/ffmpeg/ - the zip's own top-level folder name changes every
    # release, so we don't keep that layout around.
    Copy-Item -Path (Join-Path $exe.DirectoryName "*") -Destination $destDir -Recurse -Force

    Remove-Item $tmpDir -Recurse -Force
}

function Install-Poppler {
    param([string]$ToolsDir)

    $tmpDir = Join-Path $ToolsDir "poppler_tmp"
    if (Test-Path $tmpDir) { Remove-Item $tmpDir -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $tmpDir | Out-Null

    # Resolved dynamically via the GitHub API instead of a hardcoded
    # version - poppler-windows release filenames change their version
    # number every release, so a pinned URL goes stale.
    Write-Host "Consultando a versão mais recente do Poppler..."
    $release = Invoke-RestMethod -Uri "https://api.github.com/repos/oschwartz10612/poppler-windows/releases/latest" -UseBasicParsing
    $asset = $release.assets | Where-Object { $_.name -like "Release-*.zip" } | Select-Object -First 1
    if (-not $asset) {
        throw "Não foi possível encontrar o pacote mais recente do Poppler no GitHub."
    }

    $zipPath = Join-Path $tmpDir "poppler.zip"
    Write-Host "Baixando Poppler ($($asset.name))..."
    Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $zipPath -UseBasicParsing

    Write-Host "Extraindo Poppler..."
    Expand-Archive -Path $zipPath -DestinationPath $tmpDir -Force

    $exe = Get-ChildItem -Path $tmpDir -Recurse -Filter "pdftoppm.exe" | Select-Object -First 1
    if (-not $exe) {
        throw "pdftoppm.exe não foi encontrado dentro do pacote baixado."
    }

    $destDir = Join-Path $ToolsDir "poppler"
    New-Item -ItemType Directory -Force -Path $destDir | Out-Null
    Copy-Item -Path (Join-Path $exe.DirectoryName "*") -Destination $destDir -Recurse -Force

    Remove-Item $tmpDir -Recurse -Force
}

try {
    if (-not (Test-Path $ToolsDir)) {
        New-Item -ItemType Directory -Force -Path $ToolsDir | Out-Null
    }

    if ($Component -eq "ffmpeg") {
        Install-Ffmpeg -ToolsDir $ToolsDir
    }
    else {
        Install-Poppler -ToolsDir $ToolsDir
    }

    Write-Host "OK"
    exit 0
}
catch {
    Write-Host "ERRO: $($_.Exception.Message)"
    # Best-effort cleanup so a failed attempt doesn't leave a half-downloaded
    # tools/<component>_tmp folder confusing the next run.
    $tmpDir = Join-Path $ToolsDir "$($Component)_tmp"
    if (Test-Path $tmpDir) {
        Remove-Item $tmpDir -Recurse -Force -ErrorAction SilentlyContinue
    }
    exit 1
}
