<#
.SYNOPSIS
    Builds a standalone Allora.exe (PyInstaller) that bundles the Python
    interpreter and every dependency, so end users don't need Python
    installed at all. Only ffmpeg/Poppler stay external, dropped into
    tools/ next to the .exe.

.NOTES
    Run from a Python environment that already has every package from
    requirements.txt installed (`pip install -r requirements.txt`).
    Output: dist\Allora\Allora.exe (+ its _internal\ folder).
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
if (Test-Path "$root\Allora.spec") { Remove-Item "$root\Allora.spec" -Force }

python -m PyInstaller --noconfirm --windowed --name Allora `
    --icon assets\icon.ico `
    --paths src `
    --hidden-import docx2pdf `
    --collect-all pdf2docx `
    --collect-all reportlab `
    src\main.py

Write-Host ""
Write-Host "Build pronto em dist\Allora\Allora.exe"
Write-Host "Antes de distribuir, copie tools\ffmpeg e tools\poppler para dentro de dist\Allora\tools\"
