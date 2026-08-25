<#
.SYNOPSIS
    Builds a standalone MasterApp.exe (PyInstaller) that bundles the Python
    interpreter and every dependency, so end users don't need Python
    installed at all. Only ffmpeg/Poppler stay external, dropped into
    tools/ next to the .exe (same layout MasterApp.bat already uses).

.NOTES
    Run from a Python environment that already has every package from
    requirements.txt installed (MasterApp.bat --or a manual
    `pip install -r requirements.txt`-- takes care of that).
    Output: dist\MasterApp\MasterApp.exe (+ its _internal\ folder).
#>

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

python -m pip show pyinstaller *>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Instalando PyInstaller..."
    python -m pip install --quiet pyinstaller
}

if (Test-Path "$root\build") { Remove-Item "$root\build" -Recurse -Force }
if (Test-Path "$root\dist") { Remove-Item "$root\dist" -Recurse -Force }
if (Test-Path "$root\MasterApp.spec") { Remove-Item "$root\MasterApp.spec" -Force }

python -m PyInstaller --noconfirm --windowed --name MasterApp `
    --paths src `
    --hidden-import docx2pdf `
    --collect-all pdf2docx `
    --collect-all reportlab `
    src\main.py

Write-Host ""
Write-Host "Build pronto em dist\MasterApp\MasterApp.exe"
Write-Host "Antes de distribuir, copie tools\ffmpeg e tools\poppler para dentro de dist\MasterApp\tools\"
Write-Host "(rode MasterApp.bat uma vez antes, se ainda nao tiver essas pastas preenchidas)"
